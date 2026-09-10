"""Letting a socket in, and the two ways of turning one away.

A WebSocket handshake is not a CORS request. ``CORSMiddleware`` returns early
on a non-http scope and the browser sends no preflight, so none of the
protection the rest of the API gets applies here and an origin check has to be
made explicitly. ``SameSite=lax`` on the session cookie already means a
cross-site page's handshake carries no credential, which kills most of
cross-site WebSocket hijacking on its own — this is the belt to that's braces,
and it is cheap.

The two refusals are deliberately different shapes:

* **Before accept** for a bad origin. uvicorn turns a close-before-accept into
  an HTTP 403, so a page that should not be here never holds a socket at all.
* **After accept** for a bad credential. ``new WebSocket()`` cannot read a
  handshake's status code — a pre-accept 403 and a server that is down are the
  same event to a browser — so the socket is accepted for as long as it takes
  to say *why* in a frame the client can actually read, and then closed. The
  cost is a socket held for microseconds; what it buys is a UI that can tell
  "you are signed out" from "we cannot reach the server".
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit

from fastapi import WebSocket
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import resolve_principal
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings
from app.core.errors import UnauthorizedError
from app.models.activity import Channel

logger = logging.getLogger(__name__)

# --- Close codes -----------------------------------------------------------
# 4000-4999 is the range an application may define. These are the vocabulary
# the CLI daemon and the browser client both read; keep them in step with
# `app/realtime/protocol.py` and the TypeScript mirror.

UNAUTHORIZED = 4401
"""No credential, or one the server does not accept. The client should stop
reconnecting and say so — retrying will not find a token."""

FORBIDDEN = 4403
"""Authenticated, but not allowed this socket: a missing scope, or a browser
on a channel only an agent's token may use."""

NOT_FOUND = 4404
"""The project or task in the path does not exist."""

IDLE = 4408
"""Nothing said for longer than the idle window."""

DISPLACED = 4409
"""The same client session opened a socket somewhere else, and it now owns
the row. This socket writes nothing on its way out."""

BAD_FRAME = 4400
"""Sent something that is not a message this endpoint understands."""

BAD_ORIGIN = 1008
"""A policy violation in the protocol's own range, because it is sent before
the handshake completes and the application range is not available there."""


def origin_allowed(websocket: WebSocket, settings: Settings) -> bool:
    """Whether this handshake's ``Origin`` may open a socket here.

    A missing ``Origin`` is allowed: browsers always send one, and the clients
    that do not are the agents, for whom the header means nothing. The
    handshake's own host is allowed because the SPA is served from this very
    process in production, which makes it same-origin by construction.
    """
    origin = websocket.headers.get("origin")
    if origin is None:
        return True

    allowed = set(settings.cors_origins)
    host = websocket.headers.get("host")
    if host:
        for scheme in ("http", "https"):
            allowed.add(f"{scheme}://{host}")

    if origin in allowed:
        return True

    # A same-host origin on a non-default port is still this app in dev.
    split = urlsplit(origin)
    return bool(host) and split.netloc == host


async def admit(
    websocket: WebSocket,
    session: AsyncSession,
    *,
    scope: Scope,
    api_only: bool = False,
) -> Principal:
    """Resolve and authorise the caller of an accepted socket, or raise.

    Raises :class:`UnauthorizedError` or :class:`PermissionError`-shaped
    refusals for the caller to turn into a close code; it does not close the
    socket itself, because the handler owns that and has a frame to send
    first.
    """
    principal = await resolve_principal(websocket, session)

    if api_only and principal.channel is not Channel.API:
        raise SocketForbiddenError(
            "Only an agent's API token can hold this socket; the web UI cannot."
        )
    if principal.missing(frozenset({scope})):
        raise SocketForbiddenError("This token is not allowed to open that socket.")
    return principal


class SocketForbiddenError(Exception):
    """Authenticated, and still not allowed. Becomes :data:`FORBIDDEN`."""


__all__ = [
    "BAD_FRAME",
    "BAD_ORIGIN",
    "DISPLACED",
    "FORBIDDEN",
    "IDLE",
    "NOT_FOUND",
    "UNAUTHORIZED",
    "SocketForbiddenError",
    "UnauthorizedError",
    "admit",
    "origin_allowed",
]
