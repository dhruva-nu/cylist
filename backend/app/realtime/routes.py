"""The sockets themselves.

One endpoint so far: a browser watching a board. It is a one-way channel
dressed as a two-way one — the client sends nothing, the server sends
doorbells, and the client answers each by refetching over HTTP. That is the
whole protocol, and :mod:`app.realtime.hub` says why it is worth so little
and buys so much.

The session discipline here is the part to read twice. Every database access
is its own ``async with database.session()``, and none of them contains an
``await`` on the socket or the queue. Holding one across the receive loop
would pin a pooled connection for the life of a connection; fifteen of those
and every HTTP request in the process blocks for thirty seconds, ``/health``
included, and the container starts failing its healthcheck in a loop.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.scopes import Scope
from app.config import Settings
from app.core.clock import now
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.logging import bind_request_id, new_request_id, reset_request_id
from app.db import Database
from app.realtime import auth
from app.realtime.hub import Hub
from app.services import projects

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


@router.websocket("/projects/{project_ref}/board/ws")
async def board_socket(websocket: WebSocket, project_ref: str) -> None:
    """Nudge a browser whenever the board it is looking at changes.

    Sends ``{"type": "ready"}`` once the socket is good, then a
    ``{"type": "board.changed"}`` for each committed change to this project.
    Reads nothing: a client with something to say has the HTTP API for it.
    """
    settings: Settings = websocket.app.state.settings
    hub: Hub = websocket.app.state.hub
    database: Database = websocket.app.state.database

    if not auth.origin_allowed(websocket, settings):
        # Before accept, so uvicorn answers the handshake with a 403 and the
        # page never holds a socket.
        await websocket.close(code=auth.BAD_ORIGIN)
        return

    await websocket.accept()
    token = bind_request_id(new_request_id())
    opened = now()
    code = 1000
    try:
        # One short session for the whole handshake: who is this, and is the
        # project real. It answers with a value rather than by writing to the
        # socket, so that nothing is awaited on the network while a pooled
        # connection is checked out — see the module note.
        async with database.session() as session:
            outcome = await _handshake(websocket, session, project_ref)

        if isinstance(outcome, Refusal):
            code = outcome.code
            await _refuse(websocket, outcome)
            return
        project_id = outcome

        await websocket.send_json({"type": "ready"})

        with hub.watch(project_id) as watcher:
            while True:
                nudge = await watcher.queue.get()
                await websocket.send_json(nudge)
    except WebSocketDisconnect as exc:
        code = exc.code
    except asyncio.CancelledError:
        code = 1001  # going away: the server is shutting down
        raise
    finally:
        logger.info(
            "Board socket closed",
            extra={
                "context": {
                    "project": project_ref,
                    "code": code,
                    "seconds": round((now() - opened).total_seconds(), 3),
                }
            },
        )
        reset_request_id(token)


@dataclass(frozen=True)
class Refusal:
    """A decided "no", carried out of the session block to be sent."""

    code: int
    error: str
    message: str


async def _handshake(
    websocket: WebSocket,
    session: AsyncSession,
    project_ref: str,
) -> Refusal | UUID:
    """Authorise and resolve, touching only the database.

    Returns either a refusal or the project to watch. Deliberately says
    nothing on the socket: its caller holds a database session while this
    runs, and a network write there is the pool-exhaustion bug this module
    exists to avoid.
    """
    try:
        await auth.admit(websocket, session, scope=Scope.READ)
        project = await projects.resolve(session, project_ref)
    except UnauthorizedError as exc:
        return Refusal(auth.UNAUTHORIZED, "unauthorized", str(exc))
    except auth.SocketForbiddenError as exc:
        return Refusal(auth.FORBIDDEN, "forbidden", str(exc))
    except NotFoundError as exc:
        return Refusal(auth.NOT_FOUND, "not_found", str(exc))
    return project.id


async def _refuse(websocket: WebSocket, refusal: Refusal) -> None:
    """Say why, in a frame the client can read, then close.

    The order matters — see :mod:`app.realtime.auth`. A browser cannot see a
    handshake's status code, so the reason has to arrive over the accepted
    socket or not at all.
    """
    await websocket.send_json({"type": "error", "code": refusal.error, "message": refusal.message})
    await websocket.close(code=refusal.code)
