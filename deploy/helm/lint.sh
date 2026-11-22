#!/usr/bin/env bash
# Lint the chart and validate every release it is installed as: the data and
# app releases of prod and staging, and dev's data release and five slots.
# CI's "Helm chart" job runs this; so can anyone with helm and kubeconform.
set -euo pipefail

CHART="$(cd "$(dirname "${BASH_SOURCE[0]}")/cylist" && pwd)"
# Validated against the API of the cluster it is installed on.
KUBE_VERSION="${KUBE_VERSION:-1.37.0}"
# Rendered as if the monitoring stack's CRDs were installed, as they are on
# the cluster, and checked against those CRDs' published schemas.
API_VERSIONS=(--api-versions monitoring.coreos.com/v1/PodMonitor --api-versions traefik.io/v1alpha1/Middleware)
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

releases=(
  "cylist-prod cylist-data data values-prod.yaml"
  "cylist-prod cylist app values-prod.yaml"
  "cylist-staging cylist-data data values-staging.yaml"
  "cylist-staging cylist app values-staging.yaml"
  "cylist-dev cylist-data data values-dev.yaml"
)
for n in 1 2 3 4 5; do
  releases+=("cylist-dev cylist-$n app values-dev.yaml values-dev-$n.yaml")
done

status=0
for release in "${releases[@]}"; do
  read -r ns name component files <<< "$release"
  args=(--set component="$component" --set image.tag=lint)
  for f in $files; do args+=(-f "$CHART/$f"); done
  echo "→ $ns/$name ($component: $files)"
  helm lint --quiet --strict "$CHART" "${args[@]}" || status=1
  helm template "$name" "$CHART" -n "$ns" "${args[@]}" "${API_VERSIONS[@]}" |
    kubeconform -strict -summary -kubernetes-version "$KUBE_VERSION" \
      -schema-location default -schema-location "$CRD_SCHEMAS" - || status=1
done
exit "$status"
