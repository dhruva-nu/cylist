"""The sockets themselves.

Two endpoints, and they are almost opposites.

A **board** socket is a one-way channel dressed as a two-way one: the client
sends nothing, the server sends doorbells, and the client answers each by
refetching over HTTP. :mod:`app.realtime.hub` says why that is worth so
little and buys so much.

An **agent** socket is the other way round. The client does the talking, and
the connection existing is itself the claim that the session is alive — so
the important moment is not any message but the close, which ends the row
whatever caused it. That is the thing a sequence of PUTs could never do:
silence over HTTP is indistinguishable from a client with nothing to say.

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

from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import AGENT_SOCKET_IDLE_AFTER, Settings
from app.core.clock import now
from app.core.errors import NotFoundError, UnauthorizedError
from app.core.logging import bind_request_id, new_request_id, reset_request_id
from app.db import Database
from app.models.agent_session import AgentSessionReason
from app.realtime import auth, protocol
from app.realtime.hub import AgentLink, Hub, Watcher
from app.services import agent_reports, projects, tasks

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
            code = await _pump(websocket, watcher)
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


async def _pump(websocket: WebSocket, watcher: Watcher) -> int:
    """Send nudges until the client goes away. Returns the close code.

    The socket has to be *read* even though the client never says anything
    worth hearing, because reading is the only way to learn that it has gone.
    A loop that only waited on the queue would keep a dead watcher registered
    until the next nudge failed to send — which on a quiet board could be
    hours, and on a busy one is a send to nowhere per change.
    """
    nudges = asyncio.ensure_future(watcher.queue.get())
    incoming = asyncio.ensure_future(websocket.receive())
    try:
        while True:
            done, _ = await asyncio.wait({nudges, incoming}, return_when=asyncio.FIRST_COMPLETED)
            if incoming in done:
                message = incoming.result()
                if message["type"] == "websocket.disconnect":
                    return int(message.get("code", 1000))
                # Anything else a client sends here is not part of the
                # protocol. Ignored rather than refused: a board socket that
                # hung up on a stray frame would be a fragile one.
                incoming = asyncio.ensure_future(websocket.receive())
            if nudges in done:
                await websocket.send_json(nudges.result())
                nudges = asyncio.ensure_future(watcher.queue.get())
    finally:
        nudges.cancel()
        incoming.cancel()


@router.websocket("/agent-sessions/{client_session_id}/ws")
async def agent_socket(websocket: WebSocket, client_session_id: str) -> None:
    """One agent session, for as long as it is on the board.

    Replaces the heartbeat that used to be a PUT a minute. The socket being
    open *is* the report that the session is alive, so the only messages that
    cost anything are the ones that change something.

    The session id is in the path — it is the registry key, it appears in the
    log line, and it is what lets a reconnection displace the socket it left.
    The *card* is in each message, because a session walks the board.

    Closing is the point. However this ends, the row ends with it: cleanly on
    a `bye`, as `connection_lost` on anything else. That is the thing HTTP
    could not do, and the reason the board no longer has to guess from a
    clock.
    """
    settings: Settings = websocket.app.state.settings
    hub: Hub = websocket.app.state.hub
    database: Database = websocket.app.state.database

    if not auth.origin_allowed(websocket, settings):
        await websocket.close(code=auth.BAD_ORIGIN)
        return

    await websocket.accept()
    request_id = bind_request_id(new_request_id())
    opened = now()
    code = 1000
    cause = "dropped"
    link: AgentLink | None = None
    try:
        async with database.session() as session:
            outcome = await _admit_agent(websocket, session)
        if isinstance(outcome, Refusal):
            code = outcome.code
            await _refuse(websocket, outcome)
            return
        principal = outcome

        await websocket.send_json(protocol.ready(client_session_id, principal.label))
        link = AgentLink(
            client_session_id=client_session_id,
            token_id=principal.token_id,
            actor_label=principal.label,
            connected_at=opened,
        )
        hub.register_agent(link)

        code, cause = await _serve_agent(websocket, database, principal, client_session_id, link)
    except WebSocketDisconnect as exc:
        code = exc.code
    except asyncio.CancelledError:
        code = 1001
        raise
    finally:
        if link is not None:
            hub.release_agent(link)
            # A displaced socket writes nothing: the row belongs to whichever
            # connection holds the session now, and ending it here would take
            # a live agent off the board.
            if not link.displaced.is_set():
                await _end_row(database, client_session_id, cause)
        logger.info(
            "Agent socket closed",
            extra={
                "context": {
                    "session": client_session_id,
                    "code": code,
                    "cause": cause,
                    "seconds": round((now() - opened).total_seconds(), 3),
                }
            },
        )
        reset_request_id(request_id)


async def _serve_agent(
    websocket: WebSocket,
    database: Database,
    principal: Principal,
    client_session_id: str,
    link: AgentLink,
) -> tuple[int, str]:
    """Read reports until the session ends. Returns its close code and cause.

    The idle window is the receive deadline, rescheduled per message: no
    sweeper, no clock skew, and "connected and then never said anything" is
    the same case as "went quiet after an hour" rather than a special one.
    """
    loop = asyncio.get_running_loop()
    idle = AGENT_SOCKET_IDLE_AFTER.total_seconds()
    try:
        # `timeout_at`, not `timeout`: the latter takes a delay, and handing it
        # an absolute time would set the window to roughly the age of the
        # event loop — an idle timeout that never fires and looks like it works.
        async with asyncio.timeout_at(loop.time() + idle) as deadline:
            while True:
                raw = await websocket.receive_text()
                deadline.reschedule(loop.time() + idle)

                if link.displaced.is_set():
                    return auth.DISPLACED, "displaced"

                message = protocol.parse(raw)
                if message is None:
                    await websocket.send_json(
                        protocol.error("bad_frame", "That is not a message this socket takes.")
                    )
                    await websocket.close(code=auth.BAD_FRAME)
                    return auth.BAD_FRAME, "bad_frame"

                if message.type == "heartbeat":
                    # Nothing to write. The deadline has already moved, which
                    # is the entire purpose of the message.
                    continue
                if message.type == "bye":
                    await _end_row(
                        database,
                        client_session_id,
                        "bye",
                        reason=message.reason or AgentSessionReason.SESSION_ENDED,
                    )
                    await websocket.close(code=1000)
                    return 1000, "bye"

                refusal = await _apply(websocket, database, principal, client_session_id, message)
                if refusal is not None:
                    await _refuse(websocket, refusal)
                    return refusal.code, "refused"
    except TimeoutError:
        await websocket.close(code=auth.IDLE)
        return auth.IDLE, "idle"


async def _apply(
    websocket: WebSocket,
    database: Database,
    principal: Principal,
    client_session_id: str,
    message: protocol.StateMessage,
) -> Refusal | None:
    """Record one report and acknowledge it.

    The database work and the socket work are deliberately not interleaved:
    the session block closes, and only then is anything sent.
    """
    async with database.session() as session:
        try:
            task = await tasks.resolve(session, message.task)
        except NotFoundError as exc:
            return Refusal(auth.NOT_FOUND, "not_found", str(exc))
        applied = await agent_reports.report(
            session, principal, task, client_session_id, message.report
        )
    await websocket.send_json(protocol.ack(applied))
    return None


async def _end_row(
    database: Database,
    client_session_id: str,
    cause: str,
    reason: AgentSessionReason = AgentSessionReason.CONNECTION_LOST,
) -> None:
    """Close whatever row this session still holds open.

    Runs in the handler's `finally`, so it must not raise: the socket is
    already gone and there is nobody left to tell.
    """
    try:
        async with database.session() as session:
            await agent_reports.end_session(session, client_session_id, reason=reason, cause=cause)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to end the row for a closed agent socket")


async def _admit_agent(websocket: WebSocket, session: AsyncSession) -> Refusal | Principal:
    """Authorise an agent socket, touching only the database."""
    try:
        return await auth.admit(websocket, session, scope=Scope.WRITE, api_only=True)
    except UnauthorizedError as exc:
        return Refusal(auth.UNAUTHORIZED, "unauthorized", str(exc))
    except auth.SocketForbiddenError as exc:
        return Refusal(auth.FORBIDDEN, "forbidden", str(exc))


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
