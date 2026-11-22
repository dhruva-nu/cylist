#!/usr/bin/env bash
# Phase 1 (CYLIST-191): install K3s on dnu-home-1 alongside the Docker stacks.
# Run as root from the repo root:  sudo deploy/k3s/install.sh
# Re-running is safe; to upgrade, change K3S_VERSION and re-run (README.md).
set -euo pipefail

K3S_VERSION="v1.37.2+k3s1"
HELM_VERSION="v4.3.2"
POD_CIDR="10.42.0.0/16"

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
  echo "run as root: sudo $0" >&2
  exit 1
fi

# Traefik takes :80 through ServiceLB. Refuse if anything but K3s holds it.
if ss -ltnH 'sport = :80' | grep -q . && ! systemctl is-active -q k3s; then
  echo ":80 is already in use; Traefik needs it:" >&2
  ss -ltnp 'sport = :80' >&2
  exit 1
fi

# World-readable: the kubectl symlink reads it too, and it holds no secrets.
install -D -m 0644 "$here/host/config.yaml" /etc/rancher/k3s/config.yaml
install -D -m 0600 "$here/host/registries.yaml" /etc/rancher/k3s/registries.yaml
install -D -m 0600 "$here/host/traefik-config.yaml" \
  /var/lib/rancher/k3s/server/manifests/traefik-config.yaml

# ufw drops inbound by default. Pods reach the API server (6443) and the
# kubelet (10250) on the host address, which is inbound to the host, so the
# pod network must be let in. 6443 and 10250 stay closed to the LAN.
ufw allow from "$POD_CIDR" comment 'k3s pods to host'

installer="$(mktemp)"
trap 'rm -f "$installer"' EXIT
curl -sfL https://get.k3s.io -o "$installer"
INSTALL_K3S_VERSION="$K3S_VERSION" sh "$installer"

# Helm deploys Cylist (deploy/k3s/deploy.sh). Beside K3s's kubectl in
# /usr/local/bin, which is on the Actions runner's PATH; pinned and checked.
helm_tgz="$(mktemp)"
trap 'rm -f "$installer" "$helm_tgz"' EXIT
curl -sfL "https://get.helm.sh/helm-$HELM_VERSION-linux-amd64.tar.gz" -o "$helm_tgz"
echo "$(curl -sfL "https://get.helm.sh/helm-$HELM_VERSION-linux-amd64.tar.gz.sha256sum" | cut -d' ' -f1)  $helm_tgz" |
  sha256sum -c --quiet
tar -xzf "$helm_tgz" -O linux-amd64/helm > /usr/local/bin/helm.new
chmod 755 /usr/local/bin/helm.new
mv /usr/local/bin/helm.new /usr/local/bin/helm
helm version --short

echo "waiting for the node to be Ready..."
for _ in $(seq 1 60); do
  if k3s kubectl get nodes 2>/dev/null | grep -q ' Ready '; then
    break
  fi
  sleep 5
done
k3s kubectl get nodes -o wide
echo
ufw status verbose
