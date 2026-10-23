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
from typing import Any
from uuid import UUID

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import AGENT_SOCKET_IDLE_AFTER, AGENT_SOCKET_STAMP_EVERY, Settings
from app.core.clock import now
from app.core.errors import ForbiddenError, NotFoundError, UnauthorizedError
from app.core.logging import bind_request_id, new_request_id, reset_request_id
from app.db import Database
from app.models.agent_session import AgentPromptStatus, AgentSessionReason, SessionKey
from app.realtime import auth, protocol
from app.realtime.bus import Bus
from app.realtime.events import AgentConnected
from app.realtime.hub import AgentLink, Hub, Watcher
from app.services import agent_gateway, agent_reports, agent_sessions, projects

logger = logging.getLogger(__name__)

router = APIRouter(tags=["realtime"])


@dataclass(frozen=True)
class Refusal:
    """A decided "no", carried out of the session block to be sent."""

    code: int
    error: str
    message: str


AdmissionFailure = UnauthorizedError | auth.SocketForbiddenError | NotFoundError
"""Every way a handshake can be turned away, each with a close code of its own."""


@router.websocket("/projects/{project_ref}/board/ws")
async def board_socket(websocket: WebSocket, project_ref: str) -> None:
    """Nudge a browser whenever the board it is looking at changes.

    Sends ``{"type": "ready"}`` once the socket is good, then a
    ``{"type": "board.changed"}`` for each committed change to this project,
    and an ``{"type": "agent.feed", "task_id": …}`` when an agent's feed on
    one of its cards grows. Reads nothing: a client with something to say has
    the HTTP API for it.
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
            code = await _forward_nudges_until_closed(websocket, watcher)
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


async def _forward_nudges_until_closed(websocket: WebSocket, watcher: Watcher) -> int:
    """Send nudges until the client goes away. Returns the close code.

    The socket has to be *read* even though the client never says anything
    worth hearing, because reading is the only way to learn that it has gone.
    A loop that only waited on the queue would keep a dead watcher registered
    until the next nudge failed to send — which on a quiet board could be
    hours, and on a busy one is a send to nowhere per change.
    """
    nudges = asyncio.ensure_future(watcher.next())
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
                nudges = asyncio.ensure_future(watcher.next())
    finally:
        nudges.cancel()
        incoming.cancel()


@router.websocket("/agent-sessions/{client_session_id}/ws")
async def agent_socket(websocket: WebSocket, client_session_id: str) -> None:
    """One agent session, for as long as it is on the board.

    Replaces the heartbeat that used to be a PUT a minute. The socket being
    open *is* the report that the session is alive, so the only messages that
    cost anything are the ones that change something.

    The session id is in the path — with the token's person it is the
    registry key, it appears in the log line, and it is what lets a
    reconnection displace the socket it left. Only ever *the caller's*
    session of that id: someone else's is a different session, and nothing
    sent here reaches it (CYLIST-120). The *card* is in each message, because
    a session walks the board.

    While it is open the socket vouches for its session in the database —
    ``agent_session.socket_seen_at`` — so that any process, not only this
    one, can tell the session is held (CYLIST-133).

    Closing is the point. However this ends, the row ends with it: cleanly on
    a `bye`, as `connection_lost` on anything else. That is the thing HTTP
    could not do, and the reason the board no longer has to guess from a
    clock.
    """
    settings: Settings = websocket.app.state.settings
    hub: Hub = websocket.app.state.hub
    bus: Bus = websocket.app.state.bus
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
            outcome = await _admit_agent(websocket, session, client_session_id)
        if isinstance(outcome, Refusal):
            code = outcome.code
            await _refuse(websocket, outcome)
            return
        principal, key = outcome

        link = AgentLink(key=key)
        hub.register_agent(link)
        # Any other process holding an older socket for this session lets go.
        bus.publish(
            AgentConnected(
                person_id=key.person_id, client_session_id=key.client_session_id, link_id=link.id
            )
        )
        # Before `ready`, so that by the time the agent hears it, this socket
        # already holds the session and vouches for it to every process.
        await _stamp(database, link)
        await websocket.send_json(protocol.ready(client_session_id, principal.label))

        code, cause = await _serve_agent(websocket, database, principal, link)
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
                await _end_row(database, link.key, cause)
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
    link: AgentLink,
) -> tuple[int, str]:
    """Read reports until the session ends. Returns its close code and cause.

    The idle window is the receive deadline, rescheduled per message: no
    sweeper, no clock skew, and "connected and then never said anything" is
    the same case as "went quiet after an hour" rather than a special one.

    The socket is also written to between messages, since CYLIST-89: a
    permission answered on the board arrives on the link's outbox and goes
    straight down. Waiting on both is what lets an answer reach a daemon that
    has nothing to say until it hears it.
    """
    loop = asyncio.get_running_loop()
    idle = AGENT_SOCKET_IDLE_AFTER.total_seconds()
    incoming: asyncio.Future[str] = asyncio.ensure_future(websocket.receive_text())
    outgoing: asyncio.Future[dict[str, object]] = asyncio.ensure_future(link.outbox.get())
    try:
        # `timeout_at`, not `timeout`: the latter takes a delay, and handing it
        # an absolute time would set the window to roughly the age of the
        # event loop — an idle timeout that never fires and looks like it works.
        async with asyncio.timeout_at(loop.time() + idle) as deadline:
            while True:
                either: set[asyncio.Future[Any]] = {incoming, outgoing}
                done, _ = await asyncio.wait(either, return_when=asyncio.FIRST_COMPLETED)
                if outgoing in done:
                    await websocket.send_json(outgoing.result())
                    outgoing = asyncio.ensure_future(link.outbox.get())
                if incoming not in done:
                    continue
                raw = incoming.result()
                incoming = asyncio.ensure_future(websocket.receive_text())
                deadline.reschedule(loop.time() + idle)

                if link.displaced.is_set():
                    return auth.DISPLACED, "displaced"

                ending = await _handle_frame(websocket, database, principal, link.key, raw)
                if ending is not None:
                    return ending
                await _stamp(database, link)
    except TimeoutError:
        await websocket.close(code=auth.IDLE)
        return auth.IDLE, "idle"
    finally:
        incoming.cancel()
        outgoing.cancel()


async def _stamp(database: Database, link: AgentLink) -> None:
    """Vouch for this socket's session, if it has not lately.

    On connect, and then on the first frame each
    :data:`~app.config.AGENT_SOCKET_STAMP_EVERY`. A frame that ends the
    socket stamps nothing, since nothing is left to vouch for.
    """
    loop_now = asyncio.get_running_loop().time()
    due = link.stamped_at is None or (
        loop_now - link.stamped_at >= AGENT_SOCKET_STAMP_EVERY.total_seconds()
    )
    if not due:
        return
    async with database.session() as session:
        await agent_sessions.stamp_socket(session, link.key, now())
    link.stamped_at = loop_now


async def _handle_frame(
    websocket: WebSocket,
    database: Database,
    principal: Principal,
    key: SessionKey,
    raw: str,
) -> tuple[int, str] | None:
    """Act on one frame from the agent. Returns a close code and cause to end on.

    ``None`` means carry on reading. Every message but ``bye`` and a refused
    report leaves the socket open, so the loop above only has to know whether
    it is over, not what each message was.
    """
    message = protocol.parse(raw)
    if message is None:
        await websocket.send_json(
            protocol.error("bad_frame", "That is not a message this socket takes.")
        )
        await websocket.close(code=auth.BAD_FRAME)
        return auth.BAD_FRAME, "bad_frame"

    if message.type == "heartbeat":
        # Nothing to write. The deadline has already moved, which is the
        # entire purpose of the message.
        return None
    if message.type == "feed":
        # Unacknowledged, and never a reason to close: see the protocol's note
        # on `feed`.
        await _record_feed(database, principal, key, message)
        return None
    if message.type == "prompt":
        reply = await _open_prompt(database, principal, key, message)
        if reply is not None:
            await websocket.send_json(reply)
        return None
    if message.type == "withdraw":
        await _withdraw_prompt(database, principal, key, message)
        return None
    if message.type == "bye":
        await _end_row(
            database,
            key,
            "bye",
            reason=message.reason or AgentSessionReason.SESSION_ENDED,
        )
        await websocket.close(code=1000)
        return 1000, "bye"

    refusal = await _record_report(websocket, database, principal, key, message)
    if refusal is not None:
        await _refuse(websocket, refusal)
        return refusal.code, "refused"
    return None


async def _open_prompt(
    database: Database,
    principal: Principal,
    key: SessionKey,
    message: protocol.PromptMessage,
) -> dict[str, object] | None:
    """Put a permission on the card. Returns a frame to send at once, if any.

    A card that cannot be found is answered as withdrawn, so the hook stops
    waiting on a board that will never show it and the terminal decides.
    """
    async with database.session() as session:
        try:
            return await agent_gateway.open_permission(
                session,
                principal,
                key,
                message.task,
                message.prompt.id,
                message.prompt.text,
                message.prompt.tool,
            )
        except NotFoundError:
            return protocol.answer(message.prompt.id, AgentPromptStatus.WITHDRAWN)


async def _withdraw_prompt(
    database: Database,
    principal: Principal,
    key: SessionKey,
    message: protocol.WithdrawMessage,
) -> None:
    """The daemon stopped waiting. Unacknowledged: there is nothing to say back."""
    async with database.session() as session:
        try:
            await agent_gateway.withdraw_permission(
                session, principal, key, message.task, message.prompt_id, message.reason
            )
        except NotFoundError:
            # A prompt this server never kept, a card since deleted, or
            # another session's prompt: there is nothing here to be told.
            return


async def _record_report(
    websocket: WebSocket,
    database: Database,
    principal: Principal,
    key: SessionKey,
    message: protocol.StateMessage,
) -> Refusal | None:
    """Record one report and acknowledge it.

    The database work and the socket work are deliberately not interleaved:
    the session block closes, and only then is anything sent.
    """
    async with database.session() as session:
        try:
            applied = await agent_gateway.report_state(
                session, principal, key, message.task, message.report
            )
        except NotFoundError as exc:
            return Refusal(auth.NOT_FOUND, "not_found", str(exc))
    await websocket.send_json(protocol.ack(applied))
    return None


async def _record_feed(
    database: Database,
    principal: Principal,
    key: SessionKey,
    message: protocol.FeedMessage,
) -> None:
    """Keep the lines a session sent. A card that cannot be found keeps none."""
    async with database.session() as session:
        try:
            await agent_gateway.append_feed(session, principal, key, message.task, message.entries)
        except NotFoundError:
            return


async def _end_row(
    database: Database,
    key: SessionKey,
    cause: str,
    reason: AgentSessionReason = AgentSessionReason.CONNECTION_LOST,
) -> None:
    """Close whatever row this session still holds open.

    Runs in the handler's `finally`, so it must not raise: the socket is
    already gone and there is nobody left to tell.
    """
    try:
        async with database.session() as session:
            await agent_reports.end_session(session, key, reason=reason, cause=cause)
    except Exception:  # pragma: no cover - defensive
        logger.exception("Failed to end the row for a closed agent socket")


async def _admit_agent(
    websocket: WebSocket, session: AsyncSession, client_session_id: str
) -> Refusal | tuple[Principal, SessionKey]:
    """Authorise an agent socket, touching only the database.

    Returns the caller and the session it is: the path's id, as the caller's.
    """
    try:
        principal = await auth.admit(websocket, session, scope=Scope.WRITE, api_only=True)
        return principal, agent_sessions.key_for(principal, client_session_id)
    except (UnauthorizedError, auth.SocketForbiddenError) as exc:
        return _refusal_for(exc)
    except ForbiddenError as exc:
        return Refusal(auth.FORBIDDEN, "forbidden", str(exc))


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
        principal = await auth.admit(websocket, session, scope=Scope.READ)
        project = await projects.resolve_reachable(session, project_ref, principal)
    except (UnauthorizedError, auth.SocketForbiddenError, NotFoundError) as exc:
        return _refusal_for(exc)
    return project.id


def _refusal_for(exc: AdmissionFailure) -> Refusal:
    """The close code and error word an admission failure is sent with.

    One table for both sockets, so a board and an agent turned away for the
    same reason are told so in the same words.
    """
    if isinstance(exc, UnauthorizedError):
        return Refusal(auth.UNAUTHORIZED, "unauthorized", str(exc))
    if isinstance(exc, auth.SocketForbiddenError):
        return Refusal(auth.FORBIDDEN, "forbidden", str(exc))
    return Refusal(auth.NOT_FOUND, "not_found", str(exc))


async def _refuse(websocket: WebSocket, refusal: Refusal) -> None:
    """Say why, in a frame the client can read, then close.

    The order matters — see :mod:`app.realtime.auth`. A browser cannot see a
    handshake's status code, so the reason has to arrive over the accepted
    socket or not at all.
    """
    await websocket.send_json(protocol.error(refusal.error, refusal.message))
    await websocket.close(code=refusal.code)
