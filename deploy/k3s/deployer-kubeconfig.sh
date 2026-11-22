#!/usr/bin/env bash
# Write the kubeconfig the Actions runner deploys with: the cylist-deployer
# service account (platform/50-deployer-rbac.yaml), limited to the cylist-*
# namespaces, never cluster-admin. Run once, as dnu2 with the admin
# kubeconfig, after `kubectl apply -f deploy/k3s/platform/`.
#
#   deploy/k3s/deployer-kubeconfig.sh [output]   default ~/.kube/cylist-deployer.yaml
set -euo pipefail

OUT="${1:-$HOME/.kube/cylist-deployer.yaml}"
SERVER="https://127.0.0.1:6443"

token="$(kubectl -n kube-system get secret cylist-deployer-token -o jsonpath='{.data.token}' | base64 -d)"
ca="$(kubectl -n kube-system get secret cylist-deployer-token -o jsonpath='{.data.ca\.crt}')"
[[ -n "$token" && -n "$ca" ]] || { echo "cylist-deployer-token has no token yet" >&2; exit 1; }

umask 077
mkdir -p "$(dirname "$OUT")"
cat > "$OUT.new" << EOF
apiVersion: v1
kind: Config
clusters:
  - name: dnu-home-1
    cluster:
      server: $SERVER
      certificate-authority-data: $ca
users:
  - name: cylist-deployer
    user:
      token: $token
contexts:
  - name: cylist-deployer
    context:
      cluster: dnu-home-1
      user: cylist-deployer
      namespace: cylist-dev
current-context: cylist-deployer
EOF
mv "$OUT.new" "$OUT"
echo "wrote $OUT"
