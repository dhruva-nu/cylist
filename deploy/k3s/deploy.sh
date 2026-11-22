#!/usr/bin/env bash
#
# Deploy Cylist to the K3s cluster on this machine (CYLIST-193).
#
#   deploy/k3s/deploy.sh            production, namespace cylist-prod
#   deploy/k3s/deploy.sh staging    staging, namespace cylist-staging
#   deploy/k3s/deploy.sh dev N      dev slot N (1-5), namespace cylist-dev
#
# The K3s counterpart of scripts/deploy.sh, in the same order and for the same
# reasons: the image is built before anything running is touched, and the
# database is dumped before migrations run, since `alembic upgrade` is the one
# step a redeploy of the previous commit cannot undo. What differs is how each
# step is done:
#
#   build      docker build, pushed to the in-cluster registry (localhost:5000)
#   secrets    deploy/k3s/secrets.sh, from the same files the Docker stack reads
#   database   the data release (deploy/helm/cylist, component=data)
#   backup     pg_dump through kubectl exec, into $SECRETS_DIR/backups/k3s/
#   migrate    the chart's pre-upgrade hook Job
#   restart    helm upgrade --rollback-on-failure --wait (Helm 4's --atomic)
#   health     /api/v1/health through Traefik on 127.0.0.1:80, by hostname
#
# A dev slot also gets the shared database or a copy of its own, as
# scripts/dev-slot.sh decides, and the dev.cylist.in index is regenerated from
# every slot's Deployment annotations.
#
# Uses whatever kubeconfig KUBECONFIG names. CI gives it the cylist-deployer
# service account's (deploy/k3s/deployer-kubeconfig.sh), which can reach the
# cylist-* namespaces and nothing else.
#
# Until an environment is cut over (Phases 4-6), cloudflared still sends its
# hostname to the Docker stack, so a K3s deploy is reachable only through
# Traefik on this machine: curl -H 'Host: <host>' http://127.0.0.1/

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

CHART="$ROOT/deploy/helm/cylist"
REGISTRY="localhost:5000"
DATA_RELEASE="cylist-data"
POSTGRES_POD="cylist-data-postgres-0"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"

ENVIRONMENT="${1:-prod}"
SLOT=""
case "$ENVIRONMENT" in
  prod)
    NS=cylist-prod RELEASE=cylist HOST=cylist.in
    SECRETS_DIR="${CYLIST_PROD_DIR:-$HOME/cylist-prod}"
    VALUES=(-f "$CHART/values-prod.yaml")
    ;;
  staging)
    NS=cylist-staging RELEASE=cylist HOST=stage.cylist.in
    SECRETS_DIR="${CYLIST_STAGING_DIR:-$HOME/cylist-staging}"
    VALUES=(-f "$CHART/values-staging.yaml")
    ;;
  dev)
    SLOT="${2:-}"
    if [[ ! "$SLOT" =~ ^[1-5]$ ]]; then
      echo "usage: ${BASH_SOURCE[0]##*/} dev <slot 1-5>" >&2
      exit 2
    fi
    NS=cylist-dev RELEASE="cylist-$SLOT" HOST="dev$SLOT.cylist.in"
    SECRETS_DIR="${CYLIST_DEV_ROOT:-$HOME}/cylist-dev-shared"
    VALUES=(-f "$CHART/values-dev.yaml")
    ENVIRONMENT="dev-$SLOT"
    ;;
  *)
    echo "usage: ${BASH_SOURCE[0]##*/} [prod|staging|dev <slot 1-5>]" >&2
    exit 2
    ;;
esac

BACKUPS="$SECRETS_DIR/backups/k3s"

step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }
warn() {
  if [[ -n "${GITHUB_ACTIONS:-}" ]]; then echo "::warning::$1"; else echo "   warning: $1" >&2; fi
}
pg() { kubectl -n "$NS" exec -i "$POSTGRES_POD" -c postgres -- "$@"; }
# SQL as the data release's superuser, against its maintenance database.
psql_admin() {
  # shellcheck disable=SC2016  # expanded in the pod, not here
  pg sh -c 'psql -X -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d postgres' \
    <<< "SET client_min_messages = warning; $1"
}

if [[ -n "$SLOT" ]]; then
  # For its migration check and the index page. Its Docker-only steps are
  # never called from here.
  # shellcheck source=scripts/dev-slot.sh
  source "$ROOT/scripts/dev-slot.sh"
fi

BRANCH="${CYLIST_DEPLOY_BRANCH:-${GITHUB_REF_NAME:-$(git rev-parse --abbrev-ref HEAD 2> /dev/null || echo unknown)}}"
SHA="$(git rev-parse HEAD)"
TAG="$(git rev-parse --short=12 HEAD)"
# A tree with local changes is not that commit, so it never reuses its tag.
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  TAG="$TAG-dirty-$(date -u +%Y%m%d%H%M%S)"
fi
IMAGE="$REGISTRY/cylist:$TAG"

printf '\033[1mDeploying %s to K3s\033[0m (%s, %s) from %s\n' "$ENVIRONMENT" "$NS/$RELEASE" "$IMAGE" "$SECRETS_DIR"

step "Building the image"
docker build -f backend/Dockerfile --target runtime -t "$IMAGE" .
docker push --quiet "$IMAGE"

step "Updating the secrets"
"$ROOT/deploy/k3s/secrets.sh" "${ENVIRONMENT%%-*}"

step "Starting Postgres"
# Not --wait: see the note at the top of the chart's values.yaml.
helm upgrade --install "$DATA_RELEASE" "$CHART" -n "$NS" "${VALUES[@]}" \
  --set component=data
kubectl -n "$NS" rollout status "statefulset/$DATA_RELEASE-postgres" --timeout=5m
# A new pod's address reaches the network policy's allow list a moment after
# it starts; the exec below goes through the API server and needs no wait,
# but the app's migration does, and it retries on its own.

step "Backing up the database"
mkdir -p "$BACKUPS"
DUMP="$BACKUPS/cylist-$(date -u +%Y%m%dT%H%M%SZ).dump"
# shellcheck disable=SC2016  # expanded in the pod
if pg sh -c 'pg_dump --format=custom --no-owner -U "$POSTGRES_USER" "$POSTGRES_DB"' > "$DUMP" &&
  [[ -s "$DUMP" ]]; then
  echo "   $DUMP ($(du -h "$DUMP" | cut -f1))"
else
  rm -f "$DUMP"
  echo "   nothing to back up yet"
fi
# shellcheck disable=SC2012  # names are ours: cylist-<timestamp>.dump
ls -1t "$BACKUPS"/cylist-*.dump 2> /dev/null | tail -n "+$((KEEP_BACKUPS + 1))" | xargs -r rm --

EXTRA=()
DB_MODE=shared
if [[ -n "$SLOT" ]]; then
  # The same rule as the Docker slots (dev_slot_database): the shared database
  # only ever takes migrations that are on main; a branch with migrations of
  # its own runs on a copy, cylist_slot_N, made afresh from the dump above.
  step "Choosing slot $SLOT's database"
  if ! unmerged="$(_dev_unmerged_migrations)"; then
    warn "could not find origin/$DEV_MAIN_BRANCH to compare migrations with, so slot $SLOT gets a database of its own rather than risk migrating the shared one"
    unmerged="(unknown)"
  fi
  db_secret="cylist-slot-$SLOT-db"
  if [[ -z "$unmerged" ]]; then
    echo "   no migrations that $DEV_MAIN_BRANCH does not have: the shared database"
    kubectl -n "$NS" delete secret "$db_secret" --ignore-not-found > /dev/null
  else
    DB_MODE=own
    name="$(dev_slot_db_name "$SLOT")"
    echo "   migrations that $DEV_MAIN_BRANCH does not have:"
    sed 's/^/     /' <<< "$unmerged"
    echo "   so a database of its own, $name, copied from the shared one"
    psql_admin "DROP DATABASE IF EXISTS \"$name\" WITH (FORCE); CREATE DATABASE \"$name\";"
    if [[ -s "${DUMP:-}" ]]; then
      # shellcheck disable=SC2016  # expanded in the pod
      pg sh -c 'pg_restore --no-owner --exit-on-error -U "$POSTGRES_USER" -d "$1"' sh "$name" < "$DUMP"
    fi
    # The shared URL as secrets.sh wrote it, with this database's name.
    url="$(kubectl -n "$NS" get secret cylist-app -o jsonpath='{.data.CYLIST_DATABASE_URL}' | base64 -d)"
    base="${url%/*}" tail="${url##*/}" query=""
    [[ "$tail" == *\?* ]] && query="?${tail#*\?}"
    kubectl -n "$NS" create secret generic "$db_secret" \
      --from-env-file=<(printf 'CYLIST_DATABASE_URL=%s/%s%s\n' "$base" "$name" "$query") \
      --dry-run=client -o yaml | kubectl -n "$NS" apply -f - > /dev/null
  fi
  EXTRA=(-f "$CHART/values-dev-$SLOT.yaml")
fi

step "Migrating and rolling out"
# Branch names are anyone's text; they reach helm as JSON in a file, never
# through --set, which would read a comma or `=` in one as syntax.
info="$(mktemp)"
trap 'rm -f "$info"' EXIT
jq -n --arg branch "$BRANCH" --arg sha "$SHA" --arg db "$DB_MODE" \
  --arg time "$(date -u +%Y-%m-%dT%H:%M:%SZ)" --arg tag "$TAG" \
  '{image: {tag: $tag}, deployInfo: {branch: $branch, sha: $sha, time: $time, database: $db}}' > "$info"
# --rollback-on-failure puts the previous release back if the migration hook
# or the rollout fails; the schema stays wherever the migration left it, and
# the dump above is the way back from that.
helm upgrade --install "$RELEASE" "$CHART" -n "$NS" "${VALUES[@]}" "${EXTRA[@]}" \
  -f "$info" --set component=app --rollback-on-failure --wait --timeout 10m

step "Checking health"
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error --max-time 5 -H "Host: $HOST" \
    http://127.0.0.1/api/v1/health > /dev/null; then
    echo "   $HOST is answering through Traefik"
    break
  fi
  if [[ $attempt -eq 30 ]]; then
    echo "   $HOST never answered through Traefik. Recent logs:" >&2
    kubectl -n "$NS" logs "deploy/$RELEASE" --tail 50 >&2 || true
    exit 1
  fi
  sleep 2
done

if [[ -n "$SLOT" ]]; then
  if [[ "$DB_MODE" == shared ]]; then
    # Once the slot is up on the shared database, its old copy is unreachable.
    psql_admin "DROP DATABASE IF EXISTS \"$(dev_slot_db_name "$SLOT")\" WITH (FORCE);" 2> /dev/null ||
      warn "could not drop slot $SLOT's old database; it is unused and can be dropped by hand"
  fi

  step "Regenerating the index at $(dev_index_url)"
  # Two slots can deploy at once and the index is one page shared by all
  # five: read, changed, written. Serialised as on Docker.
  (
    flock 9
    rows=""
    deployments="$(kubectl -n "$NS" get deploy -l app.kubernetes.io/name=cylist,app.kubernetes.io/component=app -o json)"
    for n in "${DEV_SLOTS[@]}"; do
      row="$(jq -r --arg name "cylist-$n" '.items[] | select(.metadata.name == $name)
        | .metadata.annotations | [.["cylist.in/branch"], .["cylist.in/sha"],
          .["cylist.in/deployed-at"], .["cylist.in/database"]] | @tsv' <<< "$deployments")"
      if [[ -n "$row" ]]; then
        IFS=$'\t' read -r b s t d <<< "$row"
        rows+="$(dev_index_row "$n" "$b" "$s" "$t" "$d")"$'\n'
      else
        rows+="$(dev_index_row "$n")"$'\n'
      fi
    done
    kubectl -n "$NS" create configmap dev-index --from-file=index.html=<(dev_index_page "$rows") \
      --dry-run=client -o yaml | kubectl -n "$NS" apply -f - > /dev/null
    kubectl apply -f "$ROOT/deploy/k3s/dev-index.yaml" > /dev/null
    # A mounted ConfigMap catches up within a minute on its own; restarting
    # shows the new page now.
    kubectl -n "$NS" rollout restart deploy/dev-index > /dev/null
    kubectl -n "$NS" rollout status deploy/dev-index --timeout=2m > /dev/null
  ) 9> "$DEV_LOCK.k3s"
  echo "   dev-index ConfigMap, served at $(dev_index_url) through Traefik"

  if [[ -n "${GITHUB_OUTPUT:-}" ]]; then
    {
      echo "slot=$SLOT"
      echo "url=$(dev_slot_url "$SLOT")"
      echo "branch=$BRANCH"
      echo "sha=$SHA"
    } >> "$GITHUB_OUTPUT"
  fi
fi

printf '\n\033[1mDeployed\033[0m %s to %s on K3s (helm revision %s)\n' "$TAG" "$ENVIRONMENT" \
  "$(helm -n "$NS" history "$RELEASE" --max 1 -o json | jq -r '.[-1].revision')"
kubectl -n "$NS" get pods -l "app.kubernetes.io/instance in ($RELEASE,$DATA_RELEASE)"
