# Observability on K3s (CYLIST-194)

Metrics, logs, traces, outside-in uptime and alerts for all seven
environments and the box itself, in namespace `monitoring`. This is the
implementation of `02-observability-design.md` (project files, "K3s migration
(CYLIST-187)"), with the Phase 0 answers on CYLIST-188 applied.

```
deploy/observability/install.sh        # install or upgrade everything
python3 deploy/observability/dashboards/build.py   # after editing a dashboard
```

`install.sh` runs as `dnu2` with the cluster-admin kubeconfig and needs no
root. Re-run it after changing anything here.

## What runs

| Piece | Chart (pinned in install.sh) | What it does |
|---|---|---|
| Prometheus, Alertmanager, Grafana, node-exporter, kube-state-metrics | kube-prometheus-stack | Metrics, rules, alert routing, dashboards |
| Loki | grafana-community/loki, monolithic | Logs, 30 days |
| Tempo | grafana-community/tempo | Traces, 7 days; span metrics into Prometheus |
| Alloy | grafana/alloy, host network | Ships pod and Docker logs to Loki, scrapes cloudflared, relays OTLP to Tempo |
| blackbox-exporter | prometheus-blackbox-exporter | Probes the 8 public URLs and prod's WebSocket upgrade from outside |
| smartctl-exporter | prometheus-smartctl-exporter | The NVMe's SMART health |
| docker-cadvisor | `manifests/docker-cadvisor.yaml` | The Docker stacks' CPU and memory, for the before/after |

Retention is capped so telemetry cannot fill the disk Postgres lives on:
Prometheus 15 d or 20 GB, whichever first; Loki 30 d; Tempo 7 d. local-path
does not enforce volume sizes, and every volume shares `/`, so `DiskFilling`
and `DiskFullIn24h` are the real ceiling.

Monitoring runs at PriorityClass `cylist-platform` (50000): below production,
above staging and the dev slots, so under memory pressure a dev slot goes
before Prometheus does.

## One label: `environment`

Every series, log line and span carries `environment`: `prod`, `staging`,
`dev-1`..`dev-5`, or `dev` for dev's shared Postgres. On K3s it comes from the
`cylist.in/environment` pod label the Helm chart sets (`environment:` in each
`values-*.yaml`). For the Docker stacks it comes from the compose project
(`cylist-prod` → `prod`), and those series also carry `runtime="docker"`.
Kubernetes' own series (restarts, pod phase) carry `namespace` instead.

## Where things are

- **Grafana:** `https://dnu-home-1.tail222f46.ts.net:3443`, tailnet only
  (Phase 0, Q4), by `tailscale serve` to Grafana's pinned ClusterIP
  `10.43.200.30`. User `admin`; the password is in
  `~/cylist-observability/grafana-admin-password`. The dashboards are in the
  Cylist folder and are read-only: change `dashboards/build.py`, run it, and
  re-run `install.sh`.
- **Logs:** Grafana → Explore → Loki, for example
  `{environment="prod", container="app", level="ERROR"}`. `request_id` and
  `trace_id` are structured metadata, so filter with
  `| request_id="…"`; `trace_id` links to the trace in Tempo.
- **Traces:** Grafana → Explore → Tempo. A span links back to its log lines.

## Alerts

`manifests/rules-cylist.yaml`, plus the chart's own Kubernetes rules (minus
the scheduler, controller-manager, proxy and etcd ones, which K3s does not
expose). Each Cylist rule has a `runbook` annotation that appears in the push.

| Severity | Goes to | When |
|---|---|---|
| `page` (and the chart's `critical`) | ntfy, priority 5 | Production or the box |
| `ticket` (and everything else) | ntfy, priority 1: listed in the app, never pops up | Staging, dev, slow trends |
| `Watchdog` | Healthchecks.io, every minute | Always firing; its silence is the alarm |

Staging and dev never page: Alertmanager routes anything labelled with their
environment or namespace as a ticket, whatever the rule says.

The receivers' URLs are made by `install.sh` into the Secret
`alertmanager-urls` from `~/cylist-observability` (mode 700):

- `ntfy-topic` is generated on the first run. It is the only secret in an
  ntfy.sh topic: subscribe to it in the ntfy app (Android/iOS) to get pages.
- `healthchecks-url` is yours to create: a Healthchecks.io check with a
  1-minute period and a 5-minute grace, its ping URL in that file, and the
  check's own integration set to notify you (ntfy, email, or both). Without it
  a dead box, a dead Prometheus or a power cut pages nobody.

To test the path end to end, stop Prometheus and keep it stopped. The
operator would put it straight back, so scale the operator down first:

```sh
kubectl -n monitoring scale deploy kps-operator --replicas=0
kubectl -n monitoring scale statefulset prometheus-kps --replicas=0
# ...wait for the page, then:
kubectl -n monitoring scale statefulset prometheus-kps --replicas=1
kubectl -n monitoring scale deploy kps-operator --replicas=1
```

Alertmanager keeps the last Watchdog alive for about 3 minutes after
Prometheus stops: on the CYLIST-199 game day, Prometheus stopped at 17:18:26
and Alertmanager dropped the alert at about 17:21:30. Allow that plus the
check's period and grace before expecting the page.

What else the game day showed (2026-11-22):

- A stopped prod app container never reaches `ProdDown`. `net-watchdog`
  (`/usr/local/sbin/net-watchdog`, a systemd timer, outside this repo)
  restarts it at its second failed tick, which took 77 s, inside the alert's
  2 minutes. To test `ProdDown`, stop cloudflared instead. The watchdog does
  not touch it, and `ProdDown` paged after 2m49s.
- A stopped cloudflared does not raise `TunnelDegraded`: its series vanishes,
  and the hourly average of the samples that are left stays at 4. That case is
  covered by `HostServiceDown` (page, 4m08s) and `CloudflaredMetricsMissing`
  (ticket). `TunnelDegraded` catches connections that drop while cloudflared
  keeps running.

## Scrape targets

| Target | How |
|---|---|
| App `/metrics` (CYLIST-195) | PodMonitor in the Cylist chart, on the metrics port |
| postgres_exporter | PodMonitor in the Cylist chart (`templates/monitoring.yaml`) |
| Traefik | `manifests/scrape.yaml` |
| cloudflared `127.0.0.1:20241` | Alloy on the host network, remote-written to Prometheus |
| node, systemd units (cloudflared, k3s, docker, runner…) | node-exporter |
| Pods, Deployments, CronJobs (backup age) | kube-state-metrics |
| Docker containers | docker-cadvisor |
| Public URLs | `manifests/probes.yaml`, resolved by 1.1.1.1 not the tailnet's resolver |

The Cylist chart's PodMonitors render only where the Prometheus Operator's
CRDs exist, so the chart still installs on a bare cluster (the CI smoke test).

## Removing the Docker-era pieces (Phase 7)

When the Docker stacks are gone, delete `manifests/docker-cadvisor.yaml` (and
its line in `kustomization.yaml`), the Docker log section of
`alloy/config.alloy`, and the `docker-sock` mount in `values/alloy.yaml`. Alloy
then no longer holds the Docker socket.
