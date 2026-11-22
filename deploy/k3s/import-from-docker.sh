#!/usr/bin/env bash
#
# Copy a Docker environment's database and uploaded files into a K3s one
# (CYLIST-198): the restore step of each cutover.
#
#   deploy/k3s/import-from-docker.sh prod staging         Phase 5 rehearsal
#   deploy/k3s/import-from-docker.sh prod prod            Phase 6 cutover
#   deploy/k3s/import-from-docker.sh ... --yes            do not ask
#
# The source is read and never written: pg_dump inside its Postgres
# container, and tar over its data volume mounted read-only in a throwaway
# container, so it works whether or not the source app is running. In the
# prod cutover the source app is stopped first (04-cutover-runbook.md, step
# 2), so rows and files are from one moment; for the staging rehearsal it is
# left running, and a file uploaded between the two copies is the only skew.
#
# The target is replaced as in deploy/k3s/staging-refresh.sh: its app scaled
# to 0, the schema dropped and restored in one transaction, its blobs volume
# emptied and refilled, the app scaled back. Then every table's row count and
# the blobs' checksums are compared with the source, and each step's time is
# printed — the timing is the prod window estimate.
#
# The target's cylist-app secret must carry the source's CYLIST_VAULT_KEY, or
# the vault rows it receives are unreadable; this refuses to start otherwise.

set -euo pipefail

usage() { echo "usage: ${BASH_SOURCE[0]##*/} <docker env: prod|staging> <k3s env: prod|staging> [--yes]" >&2; exit 2; }
[[ $# -ge 2 ]] || usage
FROM="$1" TO="$2" YES="${3:-}"
[[ "$FROM" =~ ^(prod|staging)$ && "$TO" =~ ^(prod|staging)$ ]] || usage

SRC_POSTGRES="cylist-$FROM-postgres-1"
SRC_VOLUME="cylist-${FROM}_cylist-data"
SRC_ENV="${CYLIST_DOCKER_ENV_ROOT:-$HOME}/cylist-$FROM/app.env"
NS="cylist-$TO"
POSTGRES_POD=cylist-data-postgres-0
APP=deploy/cylist
BLOBS_CLAIM=cylist-data-blobs
HOST="$([[ "$TO" == prod ]] && echo cylist.in || echo stage.cylist.in)"
BUSYBOX=docker.io/library/busybox:1.37.0

step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }
started=$SECONDS
lap=$SECONDS
took() { echo "   took $((SECONDS - lap))s"; lap=$SECONDS; }
pg() { kubectl -n "$NS" exec -i "$POSTGRES_POD" -c postgres -- "$@"; }

# Every key whose name starts CYLIST_VAULT_KEY, as KEY=value lines, sorted.
src_keys="$(grep -E '^CYLIST_VAULT_KEYS?=' "$SRC_ENV" | sort)"
dst_keys="$(kubectl -n "$NS" get secret cylist-app -o json |
  jq -r '.data | to_entries[] | select(.key | test("^CYLIST_VAULT_KEYS?$")) | "\(.key)=\(.value | @base64d)"' | sort)"
if [[ -z "$src_keys" || "$src_keys" != "$dst_keys" ]]; then
  echo "refusing: $NS's cylist-app secret does not hold the same CYLIST_VAULT_KEY as $SRC_ENV." >&2
  echo "Run deploy/k3s/secrets.sh $TO from an app.env with Docker $FROM's key first." >&2
  exit 1
fi

if [[ "$YES" != "--yes" ]]; then
  cat >&2 << TXT
This replaces everything in K3s $TO ($NS) with a copy of Docker $FROM:

  database   every table dropped and reloaded from $SRC_POSTGRES
  files      $NS's blobs volume emptied and refilled from $SRC_VOLUME

Docker $FROM is only read. Type '$TO' to continue.
TXT
  read -r reply
  [[ "$reply" == "$TO" ]] || { echo "Cancelled." >&2; exit 1; }
fi

work="$(mktemp -d)"
copier=""
cleanup() {
  rm -rf "$work"
  [[ -z "$copier" ]] || kubectl -n "$NS" delete pod "$copier" --ignore-not-found --wait=false > /dev/null
}
trap cleanup EXIT

# One line per public table, "name=rows", sorted: the same on both sides or
# the copy is wrong. Exact counts, not pg_class estimates.
COUNT_SQL="select string_agg(format('select %L || ''='' || count(*) from public.%I', table_name, table_name), ' union all ' order by table_name)
  from information_schema.tables where table_schema = 'public' and table_type = 'BASE TABLE'"
# shellcheck disable=SC2016  # expanded in the container or pod
row_counts() { "$@" sh -c 'psql -XAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$(psql -XAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "$1")"' sh "$COUNT_SQL" | sort; }
# "path sha256" for every file under blobs/, sorted.
blob_sums() { "$@" sh -c 'cd /data/blobs 2> /dev/null && find . -type f -exec sha256sum {} + | sort -k2' || true; }

step "Dumping Docker $FROM"
# shellcheck disable=SC2016  # expanded in the container
docker exec "$SRC_POSTGRES" sh -c 'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$work/db.dump"
echo "   database $(du -h "$work/db.dump" | cut -f1)"
# Contents, not the directory, as staging-refresh.sh explains.
docker run --rm --network none -v "$SRC_VOLUME:/data:ro" "$BUSYBOX" \
  sh -c 'mkdir -p /tmp/e && cd /data/blobs 2> /dev/null || cd /tmp/e; ls -A | tar -cf - -T -' > "$work/blobs.tar"
echo "   blobs $(du -h "$work/blobs.tar" | cut -f1)"
row_counts docker exec "$SRC_POSTGRES" > "$work/src.counts"
blob_sums docker run --rm --network none -v "$SRC_VOLUME:/data:ro" "$BUSYBOX" > "$work/src.sums"
took

step "Stopping the $TO app"
kubectl -n "$NS" scale "$APP" --replicas=0 > /dev/null
kubectl -n "$NS" wait --for=delete pod -l app.kubernetes.io/instance=cylist,app.kubernetes.io/component=app --timeout=2m
took

step "Restoring into $NS"
# shellcheck disable=SC2016  # expanded in the pod
pg sh -c \
  '{ echo "SET client_min_messages = warning; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
     pg_restore --no-owner --no-privileges -f -
   } | psql --quiet --output=/dev/null -v ON_ERROR_STOP=1 --single-transaction \
       -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$work/db.dump"
took

step "Copying the uploaded files"
copier="blobs-import-$(date +%s)"
kubectl -n "$NS" apply -f - > /dev/null << EOF
apiVersion: v1
kind: Pod
metadata:
  name: $copier
spec:
  restartPolicy: Never
  priorityClassName: cylist-$TO
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: copy
      image: $BUSYBOX
      command: [sleep, "1800"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      volumeMounts:
        - {name: blobs, mountPath: /data/blobs}
  volumes:
    - name: blobs
      persistentVolumeClaim: {claimName: $BLOBS_CLAIM}
EOF
kubectl -n "$NS" wait --for=condition=Ready "pod/$copier" --timeout=2m > /dev/null
kubectl -n "$NS" exec -i "$copier" -- sh -c \
  'find /data/blobs -mindepth 1 -delete && cd /data/blobs && tar -xf -' < "$work/blobs.tar"
took

step "Comparing with the source"
row_counts pg > "$work/dst.counts"
blob_sums kubectl -n "$NS" exec -i "$copier" -- > "$work/dst.sums"
kubectl -n "$NS" delete pod "$copier" --wait=false > /dev/null
copier=""
if ! diff "$work/src.counts" "$work/dst.counts"; then
  echo "   row counts differ (above). The $TO app is left stopped." >&2
  exit 1
fi
if ! diff -q "$work/src.sums" "$work/dst.sums" > /dev/null; then
  diff "$work/src.sums" "$work/dst.sums" | head -20 >&2
  echo "   blobs differ (above). The $TO app is left stopped." >&2
  exit 1
fi
echo "   $(wc -l < "$work/dst.counts") tables, row counts equal"
echo "   $(wc -l < "$work/dst.sums") files, checksums equal"
# shellcheck disable=SC2016  # expanded in the pod
echo "   alembic $(pg sh -c 'psql -XAt -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "select version_num from alembic_version"')"
took

step "Starting the $TO app"
kubectl -n "$NS" scale "$APP" --replicas=1 > /dev/null
kubectl -n "$NS" rollout status "$APP" --timeout=5m
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 -H "Host: $HOST" http://127.0.0.1/api/v1/health > /dev/null; then
    echo "   $HOST is answering through Traefik"
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "   $HOST never answered. Recent logs:" >&2
    kubectl -n "$NS" logs "$APP" --tail 50 >&2 || true
    exit 1
  fi
  sleep 2
done
took

printf '\n\033[1mK3s %s now holds a copy of Docker %s\033[0m (%ss in all)\n' "$TO" "$FROM" "$((SECONDS - started))"
