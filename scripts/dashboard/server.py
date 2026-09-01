#!/usr/bin/env python3
"""Status dashboard for the three Cylist deployments on this machine.

Stdlib only — nothing to install, nothing to build. It answers two routes:
``/`` serves the static page, ``/api/status`` reports, for each environment:

  * container state and uptime, via ``docker inspect``
  * app health and request counts, via its own ``/api/v1/health`` and
    ``/api/v1/health/requests``, read over loopback so no CORS is involved
  * whether it is actually reachable the way a browser would reach it — not
    the same question as the two above. See ``_check_public`` for why.
  * how old its newest backup is

Reachable only through `tailscale serve` (see DEPLOY.md's Dashboard section),
which is why this binds loopback alone — nothing else can reach it, and this
process enforces none of that itself.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).parent
PORT = 8090

# The Tokyo ingress this tailnet's Funnel traffic actually passes through
# (see ~/.claude/CLAUDE.md — "nearest DERP blr; funnel ingress is
# ingress-tok-01"). Forcing a request through it, rather than letting MagicDNS
# resolve locally, is the only way to catch the failure mode already seen once
# here: the on-box serve config and the node both correctly believe Funnel is
# on, but the ingress-side registration has gone stale and nothing public ever
# arrives. A plain local health check cannot see that at all.
FUNNEL_INGRESS_IP = "103.84.155.153"

ENVIRONMENTS = [
    {
        "name": "prod",
        "label": "Production",
        "port": 8000,
        "url": "https://dnu-home-1.tail222f46.ts.net",
        "public_check": ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                          "--max-time", "15",
                          "--resolve", f"dnu-home-1.tail222f46.ts.net:443:{FUNNEL_INGRESS_IP}",
                          "https://dnu-home-1.tail222f46.ts.net/api/v1/health"],
    },
    {
        "name": "staging",
        "label": "Staging",
        "port": 8001,
        "url": "https://dnu-home-1.tail222f46.ts.net:8443",
        "public_check": ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                          "--max-time", "10",
                          "https://dnu-home-1.tail222f46.ts.net:8443/api/v1/health"],
    },
    {
        "name": "dev",
        "label": "Dev",
        "port": 8002,
        "url": "https://dnu-home-1.tail222f46.ts.net:9443",
        "public_check": ["curl", "-sS", "-o", "/dev/null", "-w", "%{http_code}",
                          "--max-time", "10",
                          "https://dnu-home-1.tail222f46.ts.net:9443/api/v1/health"],
    },
]

# The public checks leave the loopback interface — for prod, leave the
# tailnet entirely and come back through Tokyo — so they are seconds slower
# than everything else here. Run them on their own clock instead of blocking
# every /api/status poll on the slowest one.
PUBLIC_CHECK_INTERVAL_SECONDS = 45
_public_status: dict[str, str] = {env["name"]: "checking" for env in ENVIRONMENTS}
_public_status_lock = threading.Lock()


def _refresh_public_status() -> None:
    while True:
        for env in ENVIRONMENTS:
            try:
                out = subprocess.run(
                    env["public_check"], capture_output=True, text=True, timeout=20
                )
                reachable = out.returncode == 0 and out.stdout.strip() == "200"
            except (subprocess.TimeoutExpired, FileNotFoundError):
                reachable = False
            with _public_status_lock:
                _public_status[env["name"]] = "reachable" if reachable else "unreachable"
        time.sleep(PUBLIC_CHECK_INTERVAL_SECONDS)


def container_status(name: str) -> dict:
    """What `docker inspect` knows about this environment's app container.

    Absent entirely, distinctly from stopped, when nothing has ever deployed it
    — true of `dev` between the moment it is added here and its first
    `workflow_dispatch` run.
    """
    try:
        out = subprocess.run(
            ["docker", "inspect", f"cylist-{name}-app-1"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return {"deployed": False, "status": None, "started_at": None, "restart_count": None}

    info = json.loads(out.stdout)[0]
    return {
        "deployed": True,
        "status": info["State"]["Status"],
        "started_at": info["State"]["StartedAt"],
        "restart_count": info.get("RestartCount", 0),
    }


def _get(url: str) -> tuple[int | None, dict | None]:
    """Fetch a JSON endpoint over loopback. Returns (status code, body)."""
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        # /health answers 503 (still a JSON body) when the database is down.
        try:
            return exc.code, json.loads(exc.read())
        except ValueError:
            return exc.code, None
    except (urllib.error.URLError, OSError):
        return None, None


def app_health(port: int) -> tuple[str, dict | None]:
    code, body = _get(f"http://127.0.0.1:{port}/api/v1/health")
    if code is None:
        health = "unreachable"
    elif body and "status" in body:
        health = body["status"]
    else:
        health = "degraded"

    # None, not whatever error body came back, on an environment still
    # running an image built before this endpoint existed — it 404s there,
    # and that 404 is itself valid, parseable JSON.
    _, requests_body = _get(f"http://127.0.0.1:{port}/api/v1/health/requests")
    requests = requests_body if requests_body and "total" in requests_body else None
    return health, requests


def backup_age(name: str) -> str | None:
    """The newest dump's mtime for this environment, or ``None`` if the
    backups directory is empty or missing — dev, before its first deploy.
    """
    backups_dir = Path.home() / f"cylist-{name}" / "backups"
    try:
        dumps = list(backups_dir.glob("cylist-*.dump"))
    except OSError:
        return None
    if not dumps:
        return None
    newest = max(dumps, key=lambda p: p.stat().st_mtime)
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(newest.stat().st_mtime))


def disk_usage() -> dict:
    total, used, free = shutil.disk_usage(Path.home())
    return {
        "total_gb": round(total / 1024**3, 1),
        "used_gb": round(used / 1024**3, 1),
        "percent": round(100 * used / total, 1),
    }


def status() -> dict:
    environments = []
    for env in ENVIRONMENTS:
        container = container_status(env["name"])
        if container["deployed"]:
            health, requests = app_health(env["port"])
        else:
            health, requests = "n/a", None
        with _public_status_lock:
            public = _public_status[env["name"]] if container["deployed"] else "n/a"
        environments.append({
            "name": env["name"],
            "label": env["label"],
            "url": env["url"],
            "container": container,
            "health": health,
            "public": public,
            "requests": requests,
            "last_backup": backup_age(env["name"]),
        })
    return {"disk": disk_usage(), "environments": environments}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        pass  # this polls docker and three apps every few seconds; not worth a log line each

    def do_GET(self) -> None:
        if self.path == "/api/status":
            self._json(status())
        elif self.path in ("/", "/index.html"):
            self._file(ROOT / "index.html", "text/html")
        else:
            self.send_response(404)
            self.end_headers()

    def _json(self, payload: object) -> None:
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _file(self, path: Path, content_type: str) -> None:
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


if __name__ == "__main__":
    threading.Thread(target=_refresh_public_status, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Dashboard on http://127.0.0.1:{PORT}")
    server.serve_forever()
