"""Serving the built single-page app from the API process.

Production is one container and one port: FastAPI answers ``/api/v1`` and hands
every other path the React bundle. That is not only tidier to deploy — it is
what makes the session cookie first-party, so the browser treats it exactly as
the ``vite`` proxy makes it behave in development.

In development nothing has been built, the directory is absent, and the mount is
skipped entirely: Vite serves the app on :5173 and proxies ``/api`` to here.

One build serves any base path. Vite builds with a relative base, so the
bundle's own imports resolve against the file they are in; ``index.html`` is
the one file that cannot, since it is served at every deep link, so it is
rendered here with the base path written into its asset URLs and into the
``cylist-base-path`` meta tag the SPA reads its base path from. At the site
root that renders exactly what a ``base: '/'`` build would have emitted.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import HTMLResponse, Response
from starlette.staticfiles import StaticFiles
from starlette.types import Receive, Scope, Send

BASE_PATH_META = "cylist-base-path"
"""The ``<meta name>`` the SPA reads its base path from — ``src/api/basePath.ts``."""

_RELATIVE_ASSET = re.compile(r'(\s(?:src|href))="\./')
_BASE_PATH_TAG = re.compile(rf'<meta\s+name="{BASE_PATH_META}"\s+content="[^"]*"\s*/?>')


def render_index(document: str, base_path: str) -> str:
    """``index.html`` as served under ``base_path`` (``""`` for the site root).

    ``src="./assets/…"`` becomes ``src="/dev_1/assets/…"``: absolute, because
    this page is also the answer at ``/dev_1/p/ATL/board``, where a relative
    URL would point into ``/dev_1/p/ATL/``. And the meta tag is given the base
    path, or added if the build has none.
    """
    rendered = _RELATIVE_ASSET.sub(rf'\1="{base_path}/', document)
    tag = f'<meta name="{BASE_PATH_META}" content="{html.escape(base_path)}" />'
    if _BASE_PATH_TAG.search(rendered):
        return _BASE_PATH_TAG.sub(tag, rendered, count=1)
    if base_path and "</head>" in rendered:
        return rendered.replace("</head>", f"    {tag}\n  </head>", 1)
    return rendered


class SpaFiles(StaticFiles):
    """Static files with the history fallback a client-side router needs.

    ``/p/ATL/board`` is a path React knows about and the filesystem does not. A
    plain 404 there would break every deep link and every page reload, so a
    request that matches no file is answered with ``index.html`` and resolved by
    the router in the browser.

    The API is excluded from that fallback. ``GET /api/v1/porjects`` is a typo,
    and an agent deserves to be told so with a 404 rather than handed a page of
    HTML that its JSON parser will choke on.
    """

    def __init__(self, directory: Path, *, reserved: str, base_path: str = "") -> None:
        super().__init__(directory=directory, html=True)
        self._reserved = reserved
        # Rendered once: the build does not change under a running process.
        self._index = render_index((directory / "index.html").read_text("utf-8"), base_path)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Refuse a websocket rather than assert on one.

        ``StaticFiles`` asserts its scope is http. A mistyped socket path
        falls through the router to this mount, and the assertion is an
        unhandled exception with no close frame — the client sees the
        connection vanish and cannot tell a typo from an outage.
        """
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await super().__call__(scope, receive, send)

    async def get_response(self, path: str, scope: Scope) -> Response:
        if path in (".", "index.html"):
            return self._index_response(scope)
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or f"/{path}".startswith(self._reserved):
                raise
            return self._index_response(scope)

    def _index_response(self, scope: Scope) -> Response:
        if scope["method"] not in ("GET", "HEAD"):
            raise HTTPException(status_code=405)
        # Revalidated every time: it names the hashed bundle, so a stale copy
        # is a page asking for assets the last deploy deleted.
        return HTMLResponse(self._index, headers={"Cache-Control": "no-cache"})


def mount_spa(app: FastAPI, directory: Path, *, reserved: str, base_path: str = "") -> bool:
    """Serve the SPA in ``directory`` at ``/``, if it has been built.

    ``/`` within the app, that is: under a base path the app itself sits at
    ``base_path`` (see :mod:`app.core.base_path`), and ``base_path`` here is
    only what to write into ``index.html``.

    Returns whether it was mounted, so a caller can say which of the two modes
    it is in. Mounting at ``/`` is safe only because it happens last: the router
    and the docs are already registered, and routes are matched in order.
    """
    if not (directory / "index.html").is_file():
        return False

    app.mount("/", SpaFiles(directory, reserved=reserved, base_path=base_path), name="spa")
    return True
