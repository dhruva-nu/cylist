#!/usr/bin/env python3
"""Write the six Cylist dashboards as Grafana JSON (CYLIST-194).

    python3 deploy/observability/dashboards/build.py

The JSON is committed beside this script and loaded into Grafana by the
kustomization in ../manifests, as ConfigMaps the Grafana sidecar picks up.
Edit the dashboards here, not in Grafana: Grafana's copy is read-only and is
replaced on every install.

Every dashboard groups by `environment` (prod, staging, dev-1..dev-5, and
"dev" for dev's shared Postgres).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent

PROM = {"type": "prometheus", "uid": "prometheus"}
LOKI = {"type": "loki", "uid": "loki"}
TEMPO = {"type": "tempo", "uid": "tempo"}

Panel = dict[str, Any]


class Layout:
    """Places panels left to right on Grafana's 24-column grid."""

    def __init__(self) -> None:
        self.x = 0
        self.y = 0
        self.row_height = 0
        self.next_id = 1

    def place(self, panel: Panel, w: int, h: int) -> Panel:
        if self.x + w > 24:
            self.x, self.y = 0, self.y + self.row_height
            self.row_height = 0
        panel["gridPos"] = {"x": self.x, "y": self.y, "w": w, "h": h}
        panel["id"] = self.next_id
        self.next_id += 1
        self.x += w
        self.row_height = max(self.row_height, h)
        return panel

    def row(self, title: str) -> Panel:
        if self.x:
            self.x, self.y = 0, self.y + self.row_height
        panel = {"type": "row", "title": title, "collapsed": False, "panels": []}
        panel["gridPos"] = {"x": 0, "y": self.y, "w": 24, "h": 1}
        panel["id"] = self.next_id
        self.next_id += 1
        self.y += 1
        self.row_height = 0
        return panel


def target(expr: str, legend: str = "", ref: str = "A", **extra: Any) -> dict[str, Any]:
    return {"datasource": PROM, "expr": expr, "legendFormat": legend, "refId": ref, **extra}


def timeseries(title: str, targets: list[dict[str, Any]], unit: str = "short", **opts: Any) -> Panel:
    return {
        "type": "timeseries",
        "title": title,
        "datasource": PROM,
        "targets": targets,
        "fieldConfig": {
            "defaults": {"unit": unit, "custom": {"fillOpacity": 10, "showPoints": "never"}},
            "overrides": [],
        },
        "options": {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}},
        **opts,
    }


def stat(title: str, targets: list[dict[str, Any]], unit: str = "short", mappings: list[Any] | None = None,
         thresholds: list[dict[str, Any]] | None = None, **opts: Any) -> Panel:
    return {
        "type": "stat",
        "title": title,
        "datasource": PROM,
        "targets": targets,
        "fieldConfig": {
            "defaults": {
                "unit": unit,
                "mappings": mappings or [],
                "thresholds": {"mode": "absolute", "steps": thresholds or [{"color": "green", "value": None}]},
            },
            "overrides": [],
        },
        "options": {"colorMode": "background", "graphMode": "none", "reduceOptions": {"calcs": ["lastNotNull"]},
                    "textMode": "value_and_name"},
        **opts,
    }


def table(title: str, targets: list[dict[str, Any]], **opts: Any) -> Panel:
    return {
        "type": "table",
        "title": title,
        "datasource": PROM,
        "targets": targets,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {"showHeader": True},
        **opts,
    }


UP_DOWN = [{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"}, "1": {"text": "UP", "color": "green"}}}]
RED_GREEN = [{"color": "red", "value": None}, {"color": "green", "value": 1}]

ENV_VAR = {
    "name": "environment",
    "label": "Environment",
    "type": "custom",
    "query": "prod,staging,dev-1,dev-2,dev-3,dev-4,dev-5",
    "current": {"text": "prod", "value": "prod"},
    "options": [],
}

PG_ENV_VAR = {
    "name": "environment",
    "label": "Postgres",
    "type": "custom",
    "query": "prod,staging,dev",
    "current": {"text": "prod", "value": "prod"},
    "options": [],
}

# An environment's namespace, for Kubernetes series that carry no
# environment label: prod -> cylist-prod, dev-3 -> cylist-dev.
NS_OF_ENV = 'namespace=~"cylist-${environment:regex}|cylist-dev"'


def dashboard(uid: str, title: str, panels: list[Panel], variables: list[dict[str, Any]] | None = None,
              links: bool = True) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "tags": ["cylist"],
        "timezone": "browser",
        "editable": False,
        "graphTooltip": 1,
        "refresh": "30s",
        "time": {"from": "now-24h", "to": "now"},
        "schemaVersion": 39,
        "templating": {"list": variables or []},
        "links": [{"type": "dashboards", "tags": ["cylist"], "asDropdown": True, "title": "Cylist"}] if links else [],
        "panels": panels,
    }


# --------------------------------------------------------------- 1. overview

def estate_overview() -> dict[str, Any]:
    lay = Layout()
    p: list[Panel] = [lay.row("Public URLs, probed from outside (DNS → Cloudflare → tunnel → app)")]
    p.append(lay.place(stat(
        "Up",
        [target('probe_success{job=~"probe/monitoring/uptime-.*"}', "{{environment}}"),
         target('probe_success{job="probe/monitoring/websocket-prod"}', "prod websocket", "B")],
        mappings=UP_DOWN, thresholds=RED_GREEN,
    ), 24, 4))
    p.append(lay.place(timeseries(
        "Probe duration", [target('probe_duration_seconds{job=~"probe/monitoring/uptime-.*"}', "{{environment}}")], "s"), 12, 8))
    p.append(lay.place(timeseries(
        "Availability (1h rolling)",
        [target('avg_over_time(probe_success{job=~"probe/monitoring/uptime-.*"}[1h])', "{{environment}}")],
        "percentunit", ), 12, 8))

    p.append(lay.row("Traffic (app metrics, once CYLIST_METRICS_ENABLED is on)"))
    p.append(lay.place(timeseries(
        "Requests / s", [target('sum by (environment) (rate(http_requests_total[5m]))', "{{environment}}")], "reqps"), 8, 8))
    p.append(lay.place(timeseries(
        "5xx ratio",
        [target('sum by (environment) (rate(http_requests_total{status=~"5.."}[5m])) / sum by (environment) (rate(http_requests_total[5m]))',
                "{{environment}}")], "percentunit"), 8, 8))
    p.append(lay.place(timeseries(
        "p95 latency",
        [target('histogram_quantile(0.95, sum by (environment, le) (rate(http_request_duration_seconds_bucket{route!~".*/ws"}[5m])))',
                "{{environment}}")], "s"), 8, 8))

    p.append(lay.row("At the edge (Traefik for K3s, cloudflared for everything)"))
    p.append(lay.place(timeseries(
        "Tunnel responses / s by code",
        [target('sum by (status_code) (rate(cloudflared_tunnel_response_by_code[5m]))', "{{status_code}}")], "reqps"), 12, 8))
    p.append(lay.place(timeseries(
        "Traefik requests / s by host",
        [target('sum by (router) (rate(traefik_router_requests_total[5m]))', "{{router}}")], "reqps"), 12, 8))

    p.append(lay.row("What runs where"))
    p.append(lay.place(table(
        "K3s deploys (branch, sha, time)",
        [target('kube_deployment_annotations{namespace=~"cylist-.*"}', "", format="table", instant=True)],
        transformations=[{"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True, "__name__": True, "container": True, "endpoint": True,
                              "instance": True, "job": True, "pod": True, "service": True, "cluster": True},
            "renameByName": {"annotation_cylist_in_branch": "branch", "annotation_cylist_in_sha": "sha",
                             "annotation_cylist_in_deployed_at": "deployed at", "annotation_cylist_in_host": "host",
                             "annotation_cylist_in_database": "database"}}}],
    ), 14, 8))
    p.append(lay.place(stat(
        "Pods not ready (K3s)",
        [target('sum by (namespace) (kube_pod_status_ready{condition="false", namespace=~"cylist-.*|monitoring"})', "{{namespace}}")],
        thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 1}]), 10, 8))

    p.append(lay.row("Memory by namespace"))
    p.append(lay.place(timeseries(
        "K3s pods", [target('sum by (namespace) (container_memory_working_set_bytes{namespace=~"cylist-.*", container!=""})', "{{namespace}}")],
        "bytes"), 24, 8))
    return dashboard("cylist-estate", "Cylist / Estate overview", p)


# ---------------------------------------------------------------- 2. env detail

def environment_detail() -> dict[str, Any]:
    lay = Layout()
    env = 'environment="$environment"'
    p: list[Panel] = [lay.row("RED by route")]
    p.append(lay.place(timeseries(
        "Requests / s by route", [target(f'sum by (route) (rate(http_requests_total{{{env}}}[5m]))', "{{route}}")], "reqps"), 8, 9))
    p.append(lay.place(timeseries(
        "Errors / s by route and status",
        [target(f'sum by (route, status) (rate(http_requests_total{{{env}, status=~"5..|4.."}}[5m]))', "{{status}} {{route}}")],
        "reqps"), 8, 9))
    p.append(lay.place(timeseries(
        "p95 by route (exemplars link to traces)",
        [target(f'histogram_quantile(0.95, sum by (route, le) (rate(http_request_duration_seconds_bucket{{{env}}}[5m])))',
                "{{route}}", exemplar=True)], "s"), 8, 9))
    p.append(lay.place(timeseries(
        "In flight", [target(f'sum(http_requests_in_flight{{{env}}})', "in flight")]), 6, 7))
    p.append(lay.place(timeseries(
        "Realtime sockets",
        [target(f'sum(cylist_ws_agent_connections{{{env}}})', "agent sessions"),
         target(f'sum(cylist_ws_board_watchers{{{env}}})', "board watchers", "B")]), 6, 7))
    p.append(lay.place(timeseries(
        "Unhandled 500s and failed hooks",
        [target(f'sum(increase(cylist_unhandled_500_total{{{env}}}[5m]))', "unhandled 500s"),
         target(f'sum(increase(cylist_hook_deliveries_failed_total{{{env}}}[5m]))', "failed hooks", "B")]), 6, 7))
    p.append(lay.place(timeseries(
        "Auth refusals and rate limits",
        [target(f'sum by (reason) (rate(cylist_auth_refusals_total{{{env}}}[5m]))', "refused: {{reason}}"),
         target(f'sum by (bucket) (rate(cylist_rate_limit_hits_total{{{env}}}[5m]))', "limited: {{bucket}}", "B")], "reqps"), 6, 7))

    p.append(lay.row("Process"))
    p.append(lay.place(timeseries(
        "DB pool", [target(f'{{__name__=~"cylist_db_pool_.*", {env}}}', "{{__name__}}")]), 8, 7))
    p.append(lay.place(timeseries(
        "Event-loop lag", [target(f'{{__name__=~"cylist_event_loop_lag.*", {env}}}', "{{__name__}}")], "s"), 8, 7))
    p.append(lay.place(timeseries(
        "Memory",
        [target('sum(container_memory_working_set_bytes{container="app", pod=~"cylist-.*", '
                + NS_OF_ENV + '} * on (namespace, pod) group_left() '
                'max by (namespace, pod) (kube_pod_labels{label_cylist_in_environment="$environment"}))', "app")],
        "bytes"), 8, 7))

    p.append(lay.row("Logs and traces"))
    p.append(lay.place({
        "type": "logs", "title": "App log lines", "datasource": LOKI,
        "targets": [{"datasource": LOKI, "refId": "A", "expr": '{environment="$environment", container="app"}'}],
        "options": {"showTime": True, "wrapLogMessage": True, "sortOrder": "Descending", "enableLogDetails": True},
    }, 24, 12))
    p.append(lay.place({
        "type": "table", "title": "Slowest traces (last 1h)", "datasource": TEMPO,
        "targets": [{"datasource": TEMPO, "refId": "A", "queryType": "traceql", "limit": 20,
                     "query": '{resource.deployment.environment="$environment"} | duration > 500ms'}],
    }, 24, 8))
    return dashboard("cylist-env", "Cylist / Environment detail", p, [ENV_VAR])


# ------------------------------------------------------------------ 3. postgres

def postgres() -> dict[str, Any]:
    lay = Layout()
    env = 'environment="$environment"'
    p: list[Panel] = []
    p.append(lay.place(stat("Up", [target(f'pg_up{{{env}}}', "postgres")], mappings=UP_DOWN, thresholds=RED_GREEN), 4, 5))
    p.append(lay.place(stat("Database size", [target(f'sum(pg_database_size_bytes{{{env}, datname!~"template.*|postgres"}})', "size")],
                            "bytes"), 5, 5))
    p.append(lay.place(stat("Connections / max",
                            [target(f'sum(pg_stat_activity_count{{{env}}}) / max(pg_settings_max_connections{{{env}}})', "used")],
                            "percentunit", thresholds=[{"color": "green", "value": None}, {"color": "orange", "value": 0.6},
                                                       {"color": "red", "value": 0.8}]), 5, 5))
    p.append(lay.place(stat("Deadlocks (24h)", [target(f'sum(increase(pg_stat_database_deadlocks{{{env}}}[24h]))', "deadlocks")]), 5, 5))
    p.append(lay.place(stat("Uptime", [target(f'time() - pg_postmaster_start_time_seconds{{{env}}}', "uptime")], "s"), 5, 5))
    p.append(lay.place(timeseries(
        "Connections by state", [target(f'sum by (state) (pg_stat_activity_count{{{env}}})', "{{state}}")]), 12, 8))
    p.append(lay.place(timeseries(
        "Locks by mode", [target(f'sum by (mode) (pg_locks_count{{{env}}})', "{{mode}}")]), 12, 8))
    p.append(lay.place(timeseries(
        "Transactions / s",
        [target(f'sum(rate(pg_stat_database_xact_commit{{{env}}}[5m]))', "commits"),
         target(f'sum(rate(pg_stat_database_xact_rollback{{{env}}}[5m]))', "rollbacks", "B")], "ops"), 12, 8))
    p.append(lay.place(timeseries(
        "Size by database", [target(f'pg_database_size_bytes{{{env}, datname!~"template.*"}}', "{{datname}}")], "bytes"), 12, 8))
    p.append(lay.place(timeseries(
        "Rows / s",
        [target(f'sum(rate(pg_stat_database_tup_fetched{{{env}}}[5m]))', "fetched"),
         target(f'sum(rate(pg_stat_database_tup_inserted{{{env}}}[5m]))', "inserted", "B"),
         target(f'sum(rate(pg_stat_database_tup_updated{{{env}}}[5m]))', "updated", "C"),
         target(f'sum(rate(pg_stat_database_tup_deleted{{{env}}}[5m]))', "deleted", "D")], "ops"), 12, 8))
    p.append(lay.place(timeseries(
        "Cache hit ratio",
        [target(f'sum(rate(pg_stat_database_blks_hit{{{env}}}[5m])) / (sum(rate(pg_stat_database_blks_hit{{{env}}}[5m])) '
                f'+ sum(rate(pg_stat_database_blks_read{{{env}}}[5m])))', "hit ratio")], "percentunit"), 12, 8))
    return dashboard("cylist-postgres", "Cylist / Postgres", p, [PG_ENV_VAR])


# ---------------------------------------------------------------------- 4. edge

def edge() -> dict[str, Any]:
    lay = Layout()
    p: list[Panel] = [lay.row("cloudflared (the tunnel every public request comes through)")]
    p.append(lay.place(stat("Tunnel connections", [target("cloudflared_tunnel_ha_connections", "connections")],
                            thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 2},
                                        {"color": "green", "value": 4}]), 6, 6))
    p.append(lay.place(stat("Edge locations",
                            [target("cloudflared_tunnel_server_locations == 1", "{{edge_location}}")],
                            options={"colorMode": "none", "textMode": "name", "reduceOptions": {"calcs": ["lastNotNull"]}}), 18, 6))
    p.append(lay.place(timeseries(
        "Responses / s by status",
        [target("sum by (status_code) (rate(cloudflared_tunnel_response_by_code[5m]))", "{{status_code}}")], "reqps"), 12, 8))
    p.append(lay.place(timeseries(
        "Request errors / s", [target("rate(cloudflared_tunnel_request_errors[5m])", "errors")], "reqps"), 12, 8))

    p.append(lay.row("Traefik (K3s environments)"))
    p.append(lay.place(timeseries(
        "Requests / s by router",
        [target("sum by (router) (rate(traefik_router_requests_total[5m]))", "{{router}}")], "reqps"), 12, 8))
    p.append(lay.place(timeseries(
        "5xx / s by router",
        [target('sum by (router) (rate(traefik_router_requests_total{code=~"5.."}[5m]))', "{{router}}")], "reqps"), 12, 8))
    p.append(lay.place(timeseries(
        "p95 by router",
        [target("histogram_quantile(0.95, sum by (router, le) (rate(traefik_router_request_duration_seconds_bucket[5m])))",
                "{{router}}")], "s"), 12, 8))
    p.append(lay.place(timeseries(
        "Open connections", [target("sum by (entrypoint) (traefik_open_connections)", "{{entrypoint}}")]), 12, 8))

    p.append(lay.row("From outside"))
    p.append(lay.place(timeseries(
        "Probe phases (prod)",
        [target('probe_http_duration_seconds{job="probe/monitoring/uptime-prod"}', "{{phase}}")], "s",
        fieldConfig={"defaults": {"unit": "s", "custom": {"stacking": {"mode": "normal"}, "fillOpacity": 40}}, "overrides": []}), 12, 8))
    p.append(lay.place(stat(
        "TLS certificate expires in",
        [target('min by (environment) (probe_ssl_earliest_cert_expiry{job=~"probe/monitoring/uptime-.*"}) - time()', "{{environment}}")],
        "s", thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 14 * 86400},
                         {"color": "green", "value": 30 * 86400}]), 12, 8))
    return dashboard("cylist-edge", "Cylist / Edge", p)


# ------------------------------------------------------------ 5. node & storage

def node_storage() -> dict[str, Any]:
    lay = Layout()
    root = 'mountpoint="/", fstype!~"tmpfs|overlay"'
    p: list[Panel] = []
    p.append(lay.place(stat("Disk free", [target(f'node_filesystem_avail_bytes{{{root}}} / node_filesystem_size_bytes{{{root}}}', "/")],
                            "percentunit", thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.2},
                                                       {"color": "green", "value": 0.3}]), 6, 5))
    p.append(lay.place(stat("Memory available", [target("node_memory_MemAvailable_bytes / node_memory_MemTotal_bytes", "memory")],
                            "percentunit", thresholds=[{"color": "red", "value": None}, {"color": "orange", "value": 0.1},
                                                       {"color": "green", "value": 0.2}]), 6, 5))
    p.append(lay.place(stat("SMART", [target("smartctl_device_smart_status", "{{device}}")],
                            mappings=[{"type": "value", "options": {"0": {"text": "FAILING", "color": "red"},
                                                                    "1": {"text": "OK", "color": "green"}}}],
                            thresholds=RED_GREEN), 6, 5))
    p.append(lay.place(stat("Host services", [target('node_systemd_unit_state{state="active"}', "{{name}}")],
                            mappings=[{"type": "value", "options": {"0": {"text": "DOWN", "color": "red"},
                                                                    "1": {"text": "active", "color": "green"}}}],
                            thresholds=RED_GREEN), 6, 5))
    p.append(lay.place(timeseries(
        "Disk used on / with a 24h forecast",
        [target(f'node_filesystem_size_bytes{{{root}}} - node_filesystem_avail_bytes{{{root}}}', "used"),
         target(f'node_filesystem_size_bytes{{{root}}} - predict_linear(node_filesystem_avail_bytes{{{root}}}[6h], 24 * 3600)',
                "forecast +24h", "B"),
         target(f'node_filesystem_size_bytes{{{root}}}', "size", "C")], "bytes"), 12, 8))
    p.append(lay.place(timeseries(
        "Monitoring's own storage",
        [target("prometheus_tsdb_storage_blocks_bytes + prometheus_tsdb_wal_storage_size_bytes", "prometheus"),
         target("prometheus_tsdb_retention_limit_bytes", "prometheus cap", "B")], "bytes"), 12, 8))
    p.append(lay.place(timeseries(
        "CPU by mode",
        [target('sum by (mode) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))', "{{mode}}")], "short",
        fieldConfig={"defaults": {"unit": "short", "custom": {"stacking": {"mode": "normal"}, "fillOpacity": 40}}, "overrides": []}), 12, 8))
    p.append(lay.place(timeseries(
        "Memory",
        [target("node_memory_MemTotal_bytes - node_memory_MemAvailable_bytes", "used"),
         target("node_memory_MemTotal_bytes", "total", "B")], "bytes"), 12, 8))
    p.append(lay.place(timeseries(
        "Disk IO", [target("rate(node_disk_read_bytes_total{device=~\"nvme.*\"}[5m])", "read {{device}}"),
                    target("rate(node_disk_written_bytes_total{device=~\"nvme.*\"}[5m])", "write {{device}}", "B")], "Bps"), 12, 8))
    p.append(lay.place(timeseries(
        "Memory by namespace (K3s)",
        [target('sum by (namespace) (container_memory_working_set_bytes{container!="", namespace!=""})', "{{namespace}}")],
        "bytes"), 12, 8))
    p.append(lay.place(timeseries(
        "Disk temperature", [target('smartctl_device_temperature{temperature_type="current"}', "{{device}}")], "celsius"), 12, 8))
    p.append(lay.place(timeseries(
        "Inodes free", [target(f'node_filesystem_files_free{{{root}}} / node_filesystem_files{{{root}}}', "/")], "percentunit"), 12, 8))
    return dashboard("cylist-node", "Cylist / Node and storage", p)


# ------------------------------------------------------------------ 6. delivery

def delivery() -> dict[str, Any]:
    lay = Layout()
    p: list[Panel] = []
    p.append(lay.place(stat(
        "Deploys (7d)",
        [target('sum by (namespace) (changes(kube_deployment_status_observed_generation{namespace=~"cylist-.*", deployment=~"cylist(-[1-5])?"}[7d]))',
                "{{namespace}}")]), 12, 5))
    p.append(lay.place(stat(
        "CI runner", [target('node_systemd_unit_state{state="active", name="github-runner-cylist.service"}', "runner")],
        mappings=UP_DOWN, thresholds=RED_GREEN), 6, 5))
    p.append(lay.place(stat(
        "Last backup", [target('time() - kube_cronjob_status_last_successful_time{cronjob="cylist-data-backup"}', "{{namespace}}")],
        "s", thresholds=[{"color": "green", "value": None}, {"color": "red", "value": 26 * 3600}]), 6, 5))
    p.append(lay.place(timeseries(
        "Deploys", [target('sum by (namespace, deployment) (changes(kube_deployment_status_observed_generation{namespace=~"cylist-.*"}[1h]))',
                           "{{namespace}}/{{deployment}}")], "short",
        fieldConfig={"defaults": {"unit": "short", "custom": {"drawStyle": "bars", "fillOpacity": 80}}, "overrides": []}), 12, 8))
    p.append(lay.place(table(
        "Migration jobs: how long they took",
        [target('kube_job_status_completion_time{job_name=~".*-migrate.*"} - kube_job_status_start_time{job_name=~".*-migrate.*"}',
                "", format="table", instant=True)],
        fieldConfig={"defaults": {"unit": "s"}, "overrides": []},
        transformations=[{"id": "organize", "options": {"indexByName": {}, "excludeByName": {
            "Time": True, "__name__": True, "container": True, "endpoint": True, "instance": True, "job": True,
            "pod": True, "service": True, "cluster": True}}}]), 12, 8))
    p.append(lay.place(table(
        "What each release runs",
        [target('kube_deployment_annotations{namespace=~"cylist-.*"}', "", format="table", instant=True)],
        transformations=[{"id": "organize", "options": {
            "excludeByName": {"Time": True, "Value": True, "__name__": True, "container": True, "endpoint": True,
                              "instance": True, "job": True, "pod": True, "service": True, "cluster": True},
            "renameByName": {"annotation_cylist_in_branch": "branch", "annotation_cylist_in_sha": "sha",
                             "annotation_cylist_in_deployed_at": "deployed at", "annotation_cylist_in_host": "host",
                             "annotation_cylist_in_database": "database"}}}]), 24, 8))
    p.append(lay.place(timeseries(
        "Restarts (1h) by pod",
        [target('sum by (namespace, pod) (increase(kube_pod_container_status_restarts_total{namespace=~"cylist-.*"}[1h])) > 0',
                "{{namespace}}/{{pod}}")]), 24, 8))
    return dashboard("cylist-delivery", "Cylist / Delivery", p)


def main() -> None:
    for name, build in [
        ("estate-overview", estate_overview),
        ("environment-detail", environment_detail),
        ("postgres", postgres),
        ("edge", edge),
        ("node-storage", node_storage),
        ("delivery", delivery),
    ]:
        (HERE / f"{name}.json").write_text(json.dumps(build(), indent=2) + "\n")


if __name__ == "__main__":
    main()
