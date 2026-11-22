# K3s platform (Phase 1, CYLIST-191)

A single-node K3s cluster on dnu-home-1. It runs alongside the Docker stacks
until they are cut over (Phases 4–6) and removed (Phase 7).

| Path | Goes to | Holds |
|---|---|---|
| `host/config.yaml` | `/etc/rancher/k3s/config.yaml` | server flags: SQLite datastore, secrets encryption, kubeconfig readable by group `dnu2`, kubelet reservations |
| `host/registries.yaml` | `/etc/rancher/k3s/registries.yaml` | `localhost:5000` mirror to the in-cluster registry |
| `host/traefik-config.yaml` | `/var/lib/rancher/k3s/server/manifests/` | bundled Traefik: `:80` only, no `:443`, no WebSocket timeouts, trusted forwarded headers, Prometheus metrics |
| `platform/` | `kubectl apply` | namespaces, priority classes, quotas, network policies, registry |

## Install

```sh
sudo deploy/k3s/install.sh        # root: config files, ufw rule, K3s itself
kubectl apply -f deploy/k3s/platform/
```

`install.sh` pins `K3S_VERSION`. After it, `/etc/rancher/k3s/k3s.yaml` is
mode 0640, group `dnu2`. Use `export KUBECONFIG=/etc/rancher/k3s/k3s.yaml`, or
copy that file to `~/.kube/config`.

## Network

- Traefik's `web` entrypoint is published on `:80` by ServiceLB. cloudflared
  will reach it at `127.0.0.1:80` once routes move over (Phase 4 onwards).
- `websecure` is not published. `tailscale serve` keeps `:443` on the tailnet
  address.
- The API server (`6443`) and the kubelet (`10250`) listen on every address.
  ufw's default inbound DROP keeps them closed to the LAN. The only rule added
  is `allow from 10.42.0.0/16`, because pods reach both on the host address.
- `ufw-docker` drops forwarded SYNs to private ranges from non-private
  sources. As a result, tailnet clients (`100.64.0.0/10`) cannot reach `:80`,
  but LAN clients can.
- Traefik trusts `X-Forwarded-*` from `127.0.0.1` and the pod CIDR. ServiceLB
  presents every client as a pod IP, so a LAN client can also set
  `X-Forwarded-For`.

## Isolation

- `cylist-prod`, `cylist-staging`, `cylist-dev` and `monitoring` deny ingress
  by default. Pods accept traffic only from their own namespace, from Traefik
  and from `monitoring`. Egress is open.
- Every namespace has a ResourceQuota and a LimitRange. A container with no
  resources gets the LimitRange defaults.
- Priority classes, highest first: `cylist-prod`, `cylist-platform`,
  `cylist-staging`, `cylist-dev`. `cylist-dev` never preempts other pods.
- The kubelet reserves 1.5 CPU and 2.5 GiB for the host. It starts evicting
  pods when free memory falls below 500 MiB.

## Registry

`registry:2` runs in the `registry` namespace on the host network, bound to
`127.0.0.1:5000`, with a 30 GiB local-path volume. Push with
`docker push localhost:5000/<image>:<tag>`. Pods use the same reference, and
containerd pulls it through the mirror.

## Upgrades

Upgrades are manual. system-upgrade-controller is not used: with one node it
adds a component without saving any steps.

1. Read the release notes for the target version. Move one minor at a time.
2. Copy `/var/lib/rancher/k3s/server/db/` (SQLite) somewhere safe.
3. Set `K3S_VERSION` in `install.sh` to the new version and run
   `sudo deploy/k3s/install.sh`. The installer replaces the binary and
   restarts `k3s`. Pods keep running while it restarts.
4. Check that `kubectl get nodes` shows the new version and is `Ready`.

Check the current stable release with
`curl -s https://update.k3s.io/v1-release/channels | jq -r '.data[]|select(.id=="stable").latest'`.

## Uninstall

`sudo /usr/local/bin/k3s-uninstall.sh` removes K3s, its data and its iptables
rules. It does not touch Docker.
