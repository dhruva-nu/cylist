"""Serving the built single-page app from the API process.

Production is one container and one port: FastAPI answers ``/api/v1`` and hands
every other path the React bundle. That is not only tidier to deploy — it is
what makes the session cookie first-party, so the browser treats it exactly as
the ``vite`` proxy makes it behave in development.

In development nothing has been built, the directory is absent, and the mount is
skipped entirely: Vite serves the app on :5173 and proxies ``/api`` to here.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from starlette.exceptions import HTTPException
from starlette.responses import Response
from starlette.staticfiles import StaticFiles
from starlette.types import Scope


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

    def __init__(self, directory: Path, *, reserved: str) -> None:
        super().__init__(directory=directory, html=True)
        self._reserved = reserved

    async def get_response(self, path: str, scope: Scope) -> Response:
        try:
            return await super().get_response(path, scope)
        except HTTPException as exc:
            if exc.status_code != 404 or f"/{path}".startswith(self._reserved):
                raise
            return await super().get_response("index.html", scope)


def mount_spa(app: FastAPI, directory: Path, *, reserved: str) -> bool:
    """Serve the SPA in ``directory`` at ``/``, if it has been built.

    Returns whether it was mounted, so a caller can say which of the two modes
    it is in. Mounting at ``/`` is safe only because it happens last: the router
    and the docs are already registered, and routes are matched in order.
    """
    if not (directory / "index.html").is_file():
        return False

    app.mount("/", SpaFiles(directory, reserved=reserved), name="spa")
    return True
