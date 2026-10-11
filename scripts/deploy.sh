#!/usr/bin/env bash
#
# Deploy Cylist to this machine.
#
# The CI workflow runs exactly this script on the self-hosted runner, so a
# deploy from GitHub and a deploy you run by hand over SSH are the same code
# path — the one you can debug is the one that actually ships.
#
#   scripts/deploy.sh            production, on :8000   (the default)
#   scripts/deploy.sh staging    staging, on :8001
#   scripts/deploy.sh dev N      dev slot N (1-5), on :801N, served at :9443/dev_N
#
# One script rather than three, because the interesting part is the order of
# the steps and that order is not something staging or dev should get their own,
# drifting copy of. Only the four values below differ between the environments,
# plus what a dev slot does around them — see "Dev slots" further down.
#
# Reads the secrets that must not live in the repository from
# CYLIST_PROD_DIR (default ~/cylist-prod), CYLIST_STAGING_DIR
# (default ~/cylist-staging), or, for dev slot N, ~/cylist-dev-N (under
# CYLIST_DEV_ROOT if that is set, rather than $HOME):
#
#   $SECRETS_DIR/app.env       CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH,
#                              CYLIST_VAULT_KEY
#   $SECRETS_DIR/postgres.env  POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   $SECRETS_DIR/backups/      where this script leaves its dumps
#
# The order below is deliberate. The image is built before anything running is
# touched, so a build that does not compile changes nothing. The database is
# dumped before migrations run, because `alembic upgrade` is the one step here
# that cannot be undone by redeploying the previous commit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ENVIRONMENT="${1:-prod}"
case "$ENVIRONMENT" in
  prod)
    COMPOSE_FILE="docker-compose.prod.yml"
    SECRETS_VAR="CYLIST_PROD_DIR"
    DEFAULT_SECRETS_DIR="$HOME/cylist-prod"
    PORT=8000
    ;;
  staging)
    COMPOSE_FILE="docker-compose.staging.yml"
    SECRETS_VAR="CYLIST_STAGING_DIR"
    DEFAULT_SECRETS_DIR="$HOME/cylist-staging"
    PORT=8001
    ;;
  dev)
    SLOT="${2:-}"
    if [[ ! "$SLOT" =~ ^[1-5]$ ]]; then
      echo "usage: ${BASH_SOURCE[0]##*/} dev <slot 1-5>" >&2
      exit 2
    fi
    COMPOSE_FILE="docker-compose.dev.yml"
    SECRETS_VAR="CYLIST_DEV_DIR"
    # Set outright rather than defaulted, so a CYLIST_DEV_DIR left exported
    # from the days of the single dev stack cannot point every slot at one
    # directory.
    CYLIST_DEV_DIR="${CYLIST_DEV_ROOT:-$HOME}/cylist-dev-$SLOT"
    DEFAULT_SECRETS_DIR="$CYLIST_DEV_DIR"
    PORT=$((8010 + SLOT))
    # The compose file takes everything else about the slot from this.
    export CYLIST_DEV_SLOT="$SLOT"
    ENVIRONMENT="dev-$SLOT"
    ;;
  *)
    echo "usage: ${BASH_SOURCE[0]##*/} [prod|staging|dev <slot 1-5>]" >&2
    exit 2
    ;;
esac

# Whichever variable this environment uses, honoured if already set and exported
# either way — compose reads it back out of the environment to find app.env.
SECRETS_DIR="${!SECRETS_VAR:-$DEFAULT_SECRETS_DIR}"
export "${SECRETS_VAR}=${SECRETS_DIR}"

BACKUPS="$SECRETS_DIR/backups"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:$PORT/api/v1/health}"

compose() { docker compose -f "$COMPOSE_FILE" "$@"; }
step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }
# A line in the log, and an annotation on the run when this is CI.
warn() {
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then echo "::warning::$1"; else echo "   warning: $1" >&2; fi
}

if [[ -n "${SLOT:-}" ]]; then
  # shellcheck source=scripts/dev-slot.sh
  source "$ROOT/scripts/dev-slot.sh"
  dev_slot_prepare
fi

for f in app.env postgres.env; do
  [[ -f "$SECRETS_DIR/$f" ]] ||
    { echo "missing $SECRETS_DIR/$f — see DEPLOY.md" >&2; exit 1; }
done

printf '\033[1mDeploying %s\033[0m from %s\n' "$ENVIRONMENT" "$SECRETS_DIR"

step "Building the image"
# Both halves compile here: the Dockerfile runs `npm run build`, which
# type-checks the SPA, and `uv sync --frozen`, which fails on a stale lockfile.
compose build app

step "Starting Postgres"
compose up -d --wait postgres

step "Backing up the database"
# Inside the container, so this needs no pg_dump on the host and no published
# port. Custom format, because that is what `pg_restore` wants.
mkdir -p "$BACKUPS"
DUMP="$BACKUPS/cylist-$(date -u +%Y%m%dT%H%M%SZ).dump"
if compose exec -T postgres sh -c \
     'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DUMP"; then
  echo "   $DUMP ($(du -h "$DUMP" | cut -f1))"
else
  # A first deploy has no database to dump yet, and that must not stop it.
  rm -f "$DUMP"
  echo "   nothing to back up yet"
fi
# Keep the last few and no more: these are a safety net for the deploy that is
# happening now, not an archive. `make backup` is the archive.
ls -1t "$BACKUPS"/cylist-*.dump 2>/dev/null | tail -n "+$((KEEP_BACKUPS + 1))" |
  xargs -r rm --

step "Applying migrations"
# A one-off container rather than the app's own start-up, so a migration that
# fails fails the deploy here — visibly — instead of leaving a container
# restarting in a loop against a schema it does not match.
compose run --rm -T app alembic upgrade head

step "Starting the app"
compose up -d --wait app

step "Checking health"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 "$HEALTH_URL" > /dev/null; then
    echo "   $HEALTH_URL is answering"
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "   $HEALTH_URL never answered. Recent logs:" >&2
    compose logs --tail 50 app >&2
    exit 1
  fi
  sleep 2
done

if [[ -n "${SLOT:-}" ]]; then
  dev_slot_publish
fi

step "Tidying up"
# Only the layers nothing references. Named images and volumes are untouched.
docker image prune --force > /dev/null

printf '\n\033[1mDeployed\033[0m %s to %s\n' \
  "$(git rev-parse --short HEAD 2>/dev/null || echo "working tree")" "$ENVIRONMENT"
compose ps
