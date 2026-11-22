#!/usr/bin/env bash
# Validate what install.sh applies outside the charts: the dashboards are what
# build.py writes, and the probes, rules, scrape targets and dashboards'
# ConfigMaps match their schemas. CI's "Helm chart" job runs this.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KUBE_VERSION="${KUBE_VERSION:-1.37.0}"
CRD_SCHEMAS='https://raw.githubusercontent.com/datreeio/CRDs-catalog/main/{{.Group}}/{{.ResourceKind}}_{{.ResourceAPIVersion}}.json'

python3 "$here/dashboards/build.py"
if ! git -C "$here" diff --exit-code -- dashboards/; then
  echo "dashboards/*.json are stale: run dashboards/build.py and commit them" >&2
  exit 1
fi

kubectl kustomize "$here" |
  kubeconform -strict -summary -kubernetes-version "$KUBE_VERSION" \
    -schema-location default -schema-location "$CRD_SCHEMAS" -
