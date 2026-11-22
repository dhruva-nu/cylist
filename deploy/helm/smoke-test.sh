#!/usr/bin/env bash
# Install the chart on a throwaway K3s cluster and check it serves.
#
#   deploy/helm/smoke-test.sh <image>      e.g. cylist:ci
#
# CI runs this after building the image (.github/workflows/ci.yml, "Helm
# chart"), and it runs the same way by hand on any machine with Docker, k3d,
# helm and kubectl. It makes a k3d cluster on the K3s version dnu-home-1 runs,
# with the same namespaces, quotas and network policies (deploy/k3s/platform),
# installs the dev data release and dev slot 1 the way a deploy will, and then:
#
#   - asks /api/v1/health through Traefik, by the slot's hostname, so the
#     Ingress, the network policies, the app and Postgres are all in the path;
#   - upgrades the slot, so the migration hook runs against a migrated database;
#   - runs the nightly backup once and checks it wrote a dump and the blobs.
#
# The cluster is deleted on the way out, pass or fail.
set -euo pipefail

IMAGE="${1:?usage: ${0##*/} <image>}"
CLUSTER="${SMOKE_CLUSTER:-cylist-smoke}"
K3S_IMAGE="rancher/k3s:v1.37.2-k3s1"
HTTP_PORT="${SMOKE_HTTP_PORT:-18080}"
NS=cylist-dev

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
CHART="$ROOT/deploy/helm/cylist"
step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

cleanup() {
  local status=$?
  if [[ $status -ne 0 ]]; then
    step "Failed; what the cluster looked like"
    kubectl get pods,jobs,pvc -A -o wide || true
    kubectl -n "$NS" describe pods || true
    kubectl -n "$NS" logs -l app.kubernetes.io/name=cylist --all-containers --tail 80 || true
  fi
  k3d cluster delete "$CLUSTER" > /dev/null 2>&1 || true
  exit "$status"
}
trap cleanup EXIT

step "Creating cluster $CLUSTER ($K3S_IMAGE)"
k3d cluster create "$CLUSTER" --image "$K3S_IMAGE" --wait \
  -p "$HTTP_PORT:80@loadbalancer" --kubeconfig-switch-context=false \
  --kubeconfig-update-default=false > /dev/null
KUBECONFIG="$(mktemp)"
export KUBECONFIG
k3d kubeconfig get "$CLUSTER" > "$KUBECONFIG"
kubectl wait --for=condition=Ready node --all --timeout=120s

step "Applying the platform (not the registry: the image is imported)"
kubectl apply -f "$ROOT/deploy/k3s/platform/00-namespaces.yaml" \
  -f "$ROOT/deploy/k3s/platform/10-priorityclasses.yaml" \
  -f "$ROOT/deploy/k3s/platform/20-quotas.yaml" \
  -f "$ROOT/deploy/k3s/platform/30-networkpolicies.yaml" > /dev/null

step "Importing $IMAGE"
k3d image import "$IMAGE" -c "$CLUSTER" > /dev/null

step "Making throwaway secrets"
password="$(head -c 18 /dev/urandom | od -An -tx1 | tr -d ' \n')"
kubectl -n "$NS" create secret generic cylist-postgres \
  --from-literal=POSTGRES_USER=cylist --from-literal=POSTGRES_PASSWORD="$password" \
  --from-literal=POSTGRES_DB=cylist > /dev/null
kubectl -n "$NS" create secret generic cylist-app \
  --from-literal=CYLIST_DATABASE_URL="postgresql+asyncpg://cylist:$password@cylist-data-postgres:5432/cylist" \
  --from-literal=CYLIST_VAULT_KEY="$(head -c 32 /dev/urandom | base64 | tr '+/' '-_')" > /dev/null

repo="${IMAGE%:*}"
tag="${IMAGE##*:}"
values=(-f "$CHART/values-dev.yaml")
slot=(-f "$CHART/values-dev-1.yaml" --set component=app
  --set image.repository="$repo" --set image.tag="$tag" --set image.pullPolicy=Never)

step "Installing the data release"
# Not --wait: see the note at the top of values.yaml.
helm upgrade --install cylist-data "$CHART" -n "$NS" "${values[@]}" \
  --set component=data
kubectl -n "$NS" rollout status statefulset/cylist-data-postgres --timeout=5m

step "Installing dev slot 1"
helm upgrade --install cylist-1 "$CHART" -n "$NS" "${values[@]}" "${slot[@]}" \
  --set deployInfo.sha=smoke --wait --timeout 5m

check_health() {
  local url="http://127.0.0.1:$HTTP_PORT/api/v1/health" body
  for _ in $(seq 1 30); do
    if body="$(curl -fsS --max-time 5 -H 'Host: dev1.cylist.in' "$url")"; then
      echo "   $body"
      grep -q '"database":"up"' <<< "$body" && return 0
    fi
    sleep 2
  done
  echo "   dev1.cylist.in$url never answered healthy" >&2
  return 1
}

step "Checking health through Traefik"
check_health

step "Upgrading, so the migration hook runs again"
helm upgrade cylist-1 "$CHART" -n "$NS" "${values[@]}" "${slot[@]}" \
  --set deployInfo.sha=smoke-2 --wait --timeout 5m
check_health

step "Running the nightly backup once"
kubectl -n "$NS" create job smoke-backup --from=cronjob/cylist-data-backup > /dev/null
kubectl -n "$NS" wait --for=condition=Complete job/smoke-backup --timeout=180s
kubectl -n "$NS" logs job/smoke-backup
kubectl -n "$NS" logs job/smoke-backup | grep -q 'database.dump'
kubectl -n "$NS" logs job/smoke-backup | grep -q 'data.tar.gz'

printf '\n\033[1mThe chart installs, serves, upgrades and backs up.\033[0m\n'
