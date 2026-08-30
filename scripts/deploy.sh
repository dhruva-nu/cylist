#!/usr/bin/env bash
#
# Deploy Cylist to this machine.
#
# The CI workflow runs exactly this script on the self-hosted runner, so a
# deploy from GitHub and a deploy you run by hand over SSH are the same code
# path — the one you can debug is the one that actually ships.
#
#   scripts/deploy.sh
#
# Reads CYLIST_PROD_DIR (default ~/cylist-prod) for the secrets that must not
# live in the repository:
#
#   $CYLIST_PROD_DIR/app.env       CYLIST_DATABASE_URL, CYLIST_PASSWORD_HASH,
#                                  CYLIST_VAULT_KEY
#   $CYLIST_PROD_DIR/postgres.env  POSTGRES_USER, POSTGRES_PASSWORD, POSTGRES_DB
#   $CYLIST_PROD_DIR/backups/      where this script leaves its dumps
#
# The order below is deliberate. The image is built before anything running is
# touched, so a build that does not compile changes nothing. The database is
# dumped before migrations run, because `alembic upgrade` is the one step here
# that cannot be undone by redeploying the previous commit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export CYLIST_PROD_DIR="${CYLIST_PROD_DIR:-$HOME/cylist-prod}"
BACKUPS="$CYLIST_PROD_DIR/backups"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8000/api/v1/health}"

compose() { docker compose -f docker-compose.prod.yml "$@"; }
step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

for f in app.env postgres.env; do
  [[ -f "$CYLIST_PROD_DIR/$f" ]] ||
    { echo "missing $CYLIST_PROD_DIR/$f — see docs/DEPLOY.md" >&2; exit 1; }
done

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

step "Tidying up"
# Only the layers nothing references. Named images and volumes are untouched.
docker image prune --force > /dev/null

printf '\n\033[1mDeployed\033[0m %s\n' "$(git rev-parse --short HEAD 2>/dev/null || echo "working tree")"
compose ps
