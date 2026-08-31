#!/usr/bin/env bash
#
# Reload staging from production.
#
#   scripts/staging-refresh.sh          ask first
#   scripts/staging-refresh.sh --yes    do not ask
#
# Staging is a copy of production, which is the point of it: the bugs worth
# catching before a release are the ones that need real rows to appear. This
# script replaces staging's database *and* its uploaded blobs, together, from a
# dump taken now — the two have to move as a pair, or staging ends up with file
# records pointing at blobs it does not have.
#
# It is destructive in one direction only. Everything staging holds is dropped;
# nothing here writes to production, which is read with pg_dump and a read-only
# mount of its data volume.
#
# ⚠ Staging therefore holds real vault ciphertext, and $CYLIST_STAGING_DIR/app.env
#   must carry the *production* CYLIST_VAULT_KEY for any of it to decrypt. Treat
#   staging's secrets exactly as carefully as production's. See DEPLOY.md.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

export CYLIST_PROD_DIR="${CYLIST_PROD_DIR:-$HOME/cylist-prod}"
export CYLIST_STAGING_DIR="${CYLIST_STAGING_DIR:-$HOME/cylist-staging}"

PROD_VOLUME="${PROD_VOLUME:-cylist-prod_cylist-data}"
STAGING_VOLUME="${STAGING_VOLUME:-cylist-staging_cylist-data}"

prod()    { docker compose -f docker-compose.prod.yml "$@"; }
staging() { docker compose -f docker-compose.staging.yml "$@"; }
step()    { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

[[ -f "$CYLIST_STAGING_DIR/app.env" ]] ||
  { echo "missing $CYLIST_STAGING_DIR/app.env — see DEPLOY.md" >&2; exit 1; }

if [[ "${1:-}" != "--yes" ]]; then
  cat >&2 <<TXT
This replaces everything in STAGING with a copy of PRODUCTION:

  database   every table dropped and reloaded
  files      $STAGING_VOLUME emptied and refilled from $PROD_VOLUME

Production is only read. Type 'staging' to continue.
TXT
  read -r reply
  [[ "$reply" == "staging" ]] || { echo "Cancelled." >&2; exit 1; }
fi

step "Checking production is up"
prod up -d --wait postgres

step "Dumping production"
# Taken now rather than reusing a deploy's dump, so the rows and the blobs
# copied below are the same moment.
DUMP="$(mktemp -t cylist-prod-XXXXXX.dump)"
trap 'rm -f "$DUMP"' EXIT
prod exec -T postgres sh -c \
  'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DUMP"
echo "   $(du -h "$DUMP" | cut -f1)"

step "Stopping the staging app"
# Before the restore, not after: pg_restore --clean cannot drop tables the app
# is holding open, and a half-restored schema under a live app is worse than a
# moment of downtime on staging.
staging stop app 2>/dev/null || true
staging up -d --wait postgres

step "Restoring into staging"
# --clean --if-exists so a second run is as good as the first, and --no-owner
# because staging's database role is not production's.
staging exec -T postgres sh -c \
  'pg_restore --clean --if-exists --no-owner --no-privileges \
     -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$DUMP"

step "Copying the uploaded files"
# Production's volume is mounted read-only; only staging's is written. Ownership
# is preserved, because the app runs as uid 10001 and cannot read root's files.
docker run --rm \
  -v "$PROD_VOLUME":/from:ro \
  -v "$STAGING_VOLUME":/to \
  alpine:3 sh -c 'rm -rf /to/..?* /to/.[!.]* /to/*; cp -a /from/. /to/'

step "Starting the staging app"
staging up -d --wait app

step "Checking health"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 \
       http://127.0.0.1:8001/api/v1/health > /dev/null; then
    echo "   staging is answering on :8001"
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "   staging never answered. Recent logs:" >&2
    staging logs --tail 50 app >&2
    exit 1
  fi
  sleep 2
done

printf '\n\033[1mStaging now holds a copy of production.\033[0m\n'
