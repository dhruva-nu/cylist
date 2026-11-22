#!/usr/bin/env bash
#
# Reload staging on K3s from production on K3s (CYLIST-193).
#
#   deploy/k3s/staging-refresh.sh          ask first
#   deploy/k3s/staging-refresh.sh --yes    do not ask
#
# The K3s counterpart of scripts/staging-refresh.sh, with the same rule: the
# database and the uploaded blobs move as a pair, from the same moment, or
# staging ends up with file rows pointing at blobs it does not have. Production
# is only read, by pg_dump and by tar over its app pod's blobs mount; nothing
# here writes to cylist-prod.
#
# Staging then holds real vault ciphertext, so its cylist-app secret must carry
# production's CYLIST_VAULT_KEY (it does: secrets.sh copies ~/cylist-staging,
# which already does). Staging comes back at production's migration; Deploy
# staging migrates it forward.

set -euo pipefail

PROD=cylist-prod
STAGING=cylist-staging
POSTGRES_POD=cylist-data-postgres-0
STAGING_APP=deploy/cylist
STAGING_BLOBS=cylist-data-blobs

step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

if [[ "${1:-}" != "--yes" ]]; then
  cat >&2 << TXT
This replaces everything in STAGING (K3s, $STAGING) with a copy of PRODUCTION
(K3s, $PROD):

  database   every table dropped and reloaded
  files      staging's blobs volume emptied and refilled from production's

Production is only read. Type 'staging' to continue.
TXT
  read -r reply
  [[ "$reply" == "staging" ]] || { echo "Cancelled." >&2; exit 1; }
fi

work="$(mktemp -d)"
copier=""
cleanup() {
  rm -rf "$work"
  [[ -z "$copier" ]] || kubectl -n "$STAGING" delete pod "$copier" --ignore-not-found --wait=false > /dev/null
}
trap cleanup EXIT

step "Dumping production"
# Taken now, with the blobs right after, so rows and files are one moment.
# shellcheck disable=SC2016  # expanded in the pod
kubectl -n "$PROD" exec "$POSTGRES_POD" -c postgres -- \
  sh -c 'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$work/prod.dump"
echo "   database $(du -h "$work/prod.dump" | cut -f1)"
# The directory's contents, not the directory: its mountpoint on the other
# side belongs to root, and a non-root tar cannot set its mode.
kubectl -n "$PROD" exec deploy/cylist -c app -- \
  sh -c 'cd /data/blobs && ls -A | tar -cf - -T -' > "$work/blobs.tar"
echo "   blobs $(du -h "$work/blobs.tar" | cut -f1)"

step "Stopping the staging app"
# Before the restore: a half-restored schema under a live app is worse than a
# moment of downtime on staging.
kubectl -n "$STAGING" scale "$STAGING_APP" --replicas=0 > /dev/null
kubectl -n "$STAGING" wait --for=delete pod -l app.kubernetes.io/instance=cylist,app.kubernetes.io/component=app --timeout=2m

step "Restoring into staging"
# The whole schema, not pg_restore --clean, for the reason given in
# scripts/staging-refresh.sh; one transaction, so a failure leaves staging as
# it was.
# shellcheck disable=SC2016  # expanded in the pod
kubectl -n "$STAGING" exec -i "$POSTGRES_POD" -c postgres -- sh -c \
  '{ echo "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
     pg_restore --no-owner --no-privileges -f -
   } | psql --quiet --output=/dev/null -v ON_ERROR_STOP=1 --single-transaction \
       -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < "$work/prod.dump"

step "Copying the uploaded files"
# A short-lived pod holding staging's blobs volume while the app is down,
# running as the app's uid so the files stay the app's.
copier="blobs-refresh-$(date +%s)"
kubectl -n "$STAGING" apply -f - > /dev/null << EOF
apiVersion: v1
kind: Pod
metadata:
  name: $copier
spec:
  restartPolicy: Never
  priorityClassName: cylist-staging
  securityContext:
    runAsNonRoot: true
    runAsUser: 10001
    runAsGroup: 10001
    seccompProfile: {type: RuntimeDefault}
  containers:
    - name: copy
      image: docker.io/library/busybox:1.37.0
      command: [sleep, "600"]
      securityContext:
        allowPrivilegeEscalation: false
        readOnlyRootFilesystem: true
        capabilities: {drop: [ALL]}
      volumeMounts:
        - {name: blobs, mountPath: /data/blobs}
  volumes:
    - name: blobs
      persistentVolumeClaim: {claimName: $STAGING_BLOBS}
EOF
kubectl -n "$STAGING" wait --for=condition=Ready "pod/$copier" --timeout=2m > /dev/null
kubectl -n "$STAGING" exec -i "$copier" -- sh -c \
  'find /data/blobs -mindepth 1 -delete && cd /data/blobs && tar -xf -' < "$work/blobs.tar"
kubectl -n "$STAGING" delete pod "$copier" --wait=false > /dev/null
copier=""

step "Starting the staging app"
kubectl -n "$STAGING" scale "$STAGING_APP" --replicas=1 > /dev/null
kubectl -n "$STAGING" rollout status "$STAGING_APP" --timeout=5m

step "Checking health"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 -H 'Host: stage.cylist.in' \
    http://127.0.0.1/api/v1/health > /dev/null; then
    echo "   stage.cylist.in is answering through Traefik"
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "   staging never answered. Recent logs:" >&2
    kubectl -n "$STAGING" logs "$STAGING_APP" --tail 50 >&2 || true
    exit 1
  fi
  sleep 2
done

printf '\n\033[1mStaging on K3s now holds a copy of production on K3s.\033[0m\n'
