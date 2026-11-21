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
| `ticket` (and everything else) | ntfy, priority 2, silent | Staging, dev, slow trends |
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

To test the path end to end: `kubectl -n monitoring scale statefulset
prometheus-kps-prometheus --replicas=0` silences Watchdog, and Healthchecks.io
should page after the grace period. The operator puts the replica back on its
next reconcile, or immediately with `--replicas=1`.

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
