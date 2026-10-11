"""Serving the whole app under a path prefix, such as ``/dev_1``.

A dev slot is reached at ``https://host:9443/dev_1/`` through ``tailscale
serve --set-path /dev_1``, and tailscale strips the prefix before proxying
(``http.StripPrefix`` on the mount point; it adds no ``X-Forwarded-Prefix``).
Run locally with no proxy in front, the same request arrives with the prefix
still on. This middleware makes the two the same request, so nothing further
in has to know which one it got:

* ``path`` always starts with the base path — added back if the proxy took it
  off, left alone if it did not;
* ``root_path`` is the base path.

That is the ASGI spec's own arrangement (``path`` includes ``root_path``), so
Starlette does the rest: routing matches on the path with ``root_path`` taken
off, ``request.base_url`` — and with it the invitation link — carries the
prefix, and so does every redirect it builds.

Outermost, and only installed when a base path is configured: production and
staging, at the site root, run exactly the stack they always did.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send


def route_path(scope: Scope) -> str:
    """The path with the base path taken off: what routing matches on.

    ``/dev_1/api/v1/health`` is ``/api/v1/health``. At the site root it is
    simply the path. Starlette has the same function, privately.
    """
    path: str = scope.get("path", "-")
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        rest = path[len(root_path) :]
        if not rest or rest.startswith("/"):
            return rest or "/"
    return path


class BasePathMiddleware:
    """Put every request under ``base_path``, however it arrived."""

    def __init__(self, app: ASGIApp, *, base_path: str) -> None:
        self.app = app
        self._base = base_path
        self._base_bytes = base_path.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        raw_path: bytes | None = scope.get("raw_path")
        if path == self._base:
            # ``/dev_1`` is the slot's front door; ``/dev_1/`` is where the app is.
            path = f"{self._base}/"
            raw_path = path.encode("ascii")
        elif not path.startswith(f"{self._base}/"):
            # The proxy took the prefix off, or this is the app calling itself
            # (the hosted MCP tools reach the API in-process, unprefixed).
            path = f"{self._base}{path}"
            if raw_path is not None:
                raw_path = self._base_bytes + raw_path

        scope = dict(scope, path=path, root_path=self._base)
        if raw_path is not None:
            scope["raw_path"] = raw_path
        await self.app(scope, receive, send)
