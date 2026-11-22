#!/usr/bin/env bash
#
# Install or upgrade the monitoring stack on the K3s cluster (CYLIST-194).
#
#   deploy/observability/install.sh
#
# Run as dnu2 from the repo root, with the cluster-admin kubeconfig
# (/etc/rancher/k3s/k3s.yaml, readable by group dnu2). Re-running applies
# whatever changed; to upgrade a chart, change its version below and re-run.
#
# What it reads from ~/cylist-observability (mode 700; made on first run):
#
#   grafana-admin-password  generated if missing
#   ntfy-topic              generated if missing; subscribe to it in the ntfy
#                           app to get the pages (README.md)
#   healthchecks-url        the Healthchecks.io ping URL of the heartbeat check.
#                           Not generated: until it exists the heartbeat goes
#                           nowhere, and a dead box pages nobody.

set -euo pipefail

KPS_VERSION=93.6.1
LOKI_VERSION=21.4.3
TEMPO_VERSION=3.2.1
ALLOY_VERSION=1.16.0
BLACKBOX_VERSION=11.21.3
SMARTCTL_VERSION=0.19.2

NS=monitoring
# Grafana's Service IP (values/kube-prometheus-stack.yaml) and the tailnet
# port it is served on. Tailnet only: Phase 0, Q4.
GRAFANA_IP=10.43.200.30
GRAFANA_TAILNET_PORT=3443

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/../.." && pwd)"
export KUBECONFIG="${KUBECONFIG:-/etc/rancher/k3s/k3s.yaml}"
SECRETS="${CYLIST_OBSERVABILITY_DIR:-$HOME/cylist-observability}"

step() { printf '\n\033[1m→ %s\033[0m\n' "$1"; }

# Download a chart into $charts and print its path. Charts come from GitHub
# release assets, which time out on this line often enough (one in four, seen
# 2026-11-22) that a run with six of them rarely gets through, so the download
# alone is retried; the install after it is not.
charts="$(mktemp -d)"
trap 'rm -rf "$charts"' EXIT
fetch() {
  local repo_url=$1 name=$2 version=$3 attempt
  for attempt in 1 2 3 4 5; do
    if helm pull "$name" --repo "$repo_url" --version "$version" -d "$charts" > /dev/null; then
      echo "$charts/$name-$version.tgz"
      return
    fi
    echo "   download of $name $version failed (attempt $attempt); retrying" >&2
    sleep $((attempt * 5))
  done
  return 1
}

step "Platform manifests (namespaces, priorities, quotas, policies)"
kubectl apply -f "$repo/deploy/k3s/platform/"

step "Secrets from $SECRETS"
install -d -m 700 "$SECRETS"
[[ -s "$SECRETS/grafana-admin-password" ]] ||
  (umask 077 && openssl rand -base64 24 | tr -d '\n' > "$SECRETS/grafana-admin-password")
[[ -s "$SECRETS/ntfy-topic" ]] ||
  (umask 077 && printf 'cylist-%s' "$(openssl rand -hex 12)" > "$SECRETS/ntfy-topic")
if [[ -s "$SECRETS/healthchecks-url" ]]; then
  heartbeat="$(< "$SECRETS/healthchecks-url")"
else
  # A URL nothing answers: Alertmanager logs the failure and carries on.
  heartbeat="http://127.0.0.1:9/no-heartbeat-configured"
  echo "   WARNING: no $SECRETS/healthchecks-url; the heartbeat is not wired up" >&2
fi
topic="$(< "$SECRETS/ntfy-topic")"

# ntfy renders the title and message from Alertmanager's JSON (tpl=yes), so
# the push reads as words rather than a JSON blob. page rings; ticket is a
# notification that never pops up (ntfy priority 1): it waits in the app
# for the morning.
ntfy_url() {
  python3 - "$topic" "$1" "$2" << 'PY'
import sys, urllib.parse
topic, priority, tags = sys.argv[1:]
title = '{{if eq .status "firing"}}🔥{{else}}✅{{end}} {{.commonLabels.alertname}}{{with .commonLabels.environment}} ({{.}}){{end}}'
message = '{{range .alerts}}{{.status}}: {{.annotations.summary}}\n{{end}}{{with (index .alerts 0).annotations.runbook}}\n{{.}}{{end}}'
query = urllib.parse.urlencode({"tpl": "yes", "t": title, "m": message, "p": priority, "tags": tags})
print(f"https://ntfy.sh/{topic}?{query}", end="")
PY
}

kubectl apply -f - > /dev/null << EOF
apiVersion: v1
kind: Secret
metadata:
  name: grafana-admin
  namespace: $NS
type: Opaque
stringData:
  admin-user: admin
  admin-password: "$(< "$SECRETS/grafana-admin-password")"
---
apiVersion: v1
kind: Secret
metadata:
  name: alertmanager-urls
  namespace: $NS
type: Opaque
stringData:
  page: '$(ntfy_url 5 rotating_light)'
  ticket: '$(ntfy_url 1 memo)'
  heartbeat: '$heartbeat'
EOF
echo "   grafana-admin, alertmanager-urls"

step "kube-prometheus-stack $KPS_VERSION"
chart="$(fetch https://prometheus-community.github.io/helm-charts kube-prometheus-stack "$KPS_VERSION")"
helm upgrade --install kps "$chart" -n "$NS" \
  -f "$here/values/kube-prometheus-stack.yaml" --wait --timeout 20m

step "Loki $LOKI_VERSION"
chart="$(fetch https://grafana-community.github.io/helm-charts loki "$LOKI_VERSION")"
helm upgrade --install loki "$chart" -n "$NS" \
  -f "$here/values/loki.yaml" --wait --timeout 20m

step "Tempo $TEMPO_VERSION"
chart="$(fetch https://grafana-community.github.io/helm-charts tempo "$TEMPO_VERSION")"
helm upgrade --install tempo "$chart" -n "$NS" \
  -f "$here/values/tempo.yaml" --wait --timeout 20m

step "Alloy $ALLOY_VERSION"
chart="$(fetch https://grafana.github.io/helm-charts alloy "$ALLOY_VERSION")"
helm upgrade --install alloy "$chart" -n "$NS" \
  -f "$here/values/alloy.yaml" \
  --set-file alloy.configMap.content="$here/alloy/config.alloy" --wait --timeout 20m

step "blackbox-exporter $BLACKBOX_VERSION"
chart="$(fetch https://prometheus-community.github.io/helm-charts prometheus-blackbox-exporter "$BLACKBOX_VERSION")"
helm upgrade --install blackbox "$chart" -n "$NS" \
  -f "$here/values/blackbox.yaml" --wait --timeout 15m

step "smartctl-exporter $SMARTCTL_VERSION"
chart="$(fetch https://prometheus-community.github.io/helm-charts prometheus-smartctl-exporter "$SMARTCTL_VERSION")"
helm upgrade --install smartctl "$chart" -n "$NS" \
  -f "$here/values/smartctl.yaml" --wait --timeout 15m

step "Probes, scrape targets, alert rules and dashboards"
kubectl apply -k "$here"

step "Grafana on the tailnet"
tailscale serve --bg --yes --https="$GRAFANA_TAILNET_PORT" "http://$GRAFANA_IP:80" > /dev/null
echo "   https://$(tailscale status --json | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'):$GRAFANA_TAILNET_PORT"
echo "   user admin, password in $SECRETS/grafana-admin-password"
echo "   pages: subscribe to ntfy.sh topic $(< "$SECRETS/ntfy-topic")"

kubectl -n "$NS" get pods
