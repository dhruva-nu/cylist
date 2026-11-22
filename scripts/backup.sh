#!/usr/bin/env bash
#
# Back up a Cylist installation.
#
# A complete backup is two things, and a restore needs both:
#
#   1. the database  — projects, boards, people, and the *encrypted* vault rows
#   2. the data dir  — the uploaded file blobs those rows point at
#
# It does not include CYLIST_VAULT_KEY. That is deliberate: a backup that
# carries its own decryption key is not much of a safeguard. Keep the key
# somewhere else you can recover it — without it the vault rows in this
# backup are unreadable.
#
# Usage:
#   scripts/backup.sh [destination-directory]
#
# Reads CYLIST_DATABASE_URL and CYLIST_DATA_DIR, falling back to backend/.env.
#
# The dump must be readable by the pg_restore that will restore it, and a
# pg_dump newer than the server writes a format older tools reject. So the
# client may not be newer than the server:
#
#   PG_DUMP=/usr/lib/postgresql/16/bin/pg_dump   use a client that matches
#   CYLIST_BACKUP_CONTAINER=<postgres container> dump inside that container
#                                                (its own client, no host
#                                                pg_dump needed)

set -euo pipefail

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../backend" && pwd)"
DESTINATION="${1:-backups}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

# Values already in the environment win; anything missing comes from .env.
if [[ -f "$BACKEND_DIR/.env" ]]; then
  while IFS='=' read -r key value; do
    [[ $key == CYLIST_* ]] || continue
    [[ -n ${!key:-} ]] || printf -v "$key" '%s' "$value"
  done < <(grep -E '^CYLIST_[A-Z_]+=' "$BACKEND_DIR/.env" || true)
fi

: "${CYLIST_DATABASE_URL:?Set CYLIST_DATABASE_URL or put it in backend/.env}"
DATA_DIR="${CYLIST_DATA_DIR:-$BACKEND_DIR/data}"

# pg_dump speaks libpq, which does not know the SQLAlchemy driver suffix.
DUMP_URL="${CYLIST_DATABASE_URL/postgresql+asyncpg:\/\//postgresql://}"

TARGET="$DESTINATION/cylist-$STAMP"
mkdir -p "$TARGET"

echo "→ database"
if [[ -n ${CYLIST_BACKUP_CONTAINER:-} ]]; then
  # Same major as the server by construction. Credentials come from the
  # container's own environment.
  docker exec "$CYLIST_BACKUP_CONTAINER" sh -c \
    'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' \
    > "$TARGET/database.dump"
else
  PG_DUMP="${PG_DUMP:-pg_dump}"
  client_major="$("$PG_DUMP" --version | sed -E 's/[^0-9]*([0-9]+).*/\1/')"
  server_major="$(psql -At "$DUMP_URL" -c 'show server_version_num' | cut -c1-2)"
  if (( client_major > server_major )); then
    echo "refusing: $PG_DUMP is version $client_major but the server is $server_major." >&2
    echo "A dump from the newer client cannot be restored by a $server_major pg_restore." >&2
    echo "Set PG_DUMP to a $server_major client, or CYLIST_BACKUP_CONTAINER to dump inside the container." >&2
    exit 1
  fi
  "$PG_DUMP" --format=custom --no-owner --file "$TARGET/database.dump" "$DUMP_URL"
fi

echo "→ uploaded files"
if [[ -d "$DATA_DIR" ]]; then
  tar --create --gzip --file "$TARGET/data.tar.gz" --directory "$DATA_DIR" .
else
  echo "   (no data directory at $DATA_DIR — nothing uploaded yet)"
fi

cat > "$TARGET/RESTORE.md" <<'NOTES'
# Restoring this backup

    createdb cylist
    pg_restore --dbname "$CYLIST_DATABASE_URL" --no-owner database.dump
    mkdir -p "$CYLIST_DATA_DIR" && tar -xzf data.tar.gz -C "$CYLIST_DATA_DIR"

Use a pg_restore at least as new as the server that was dumped, and a
server at least as new as the dump's client.

Then set CYLIST_VAULT_KEY to the key this installation used. It is not in
this backup, and without it every vault secret here stays unreadable.
NOTES

echo
echo "Backed up to $TARGET"
du -sh "$TARGET"/* | sed 's/^/  /'
echo
echo "Remember: CYLIST_VAULT_KEY is not in here. Store it separately."
