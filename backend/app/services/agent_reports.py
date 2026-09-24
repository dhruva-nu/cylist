"""Recording what an agent session is doing, however the news arrived.

:mod:`app.services.agent_sessions` owns the two hard parts — :func:`upsert`,
which applies one report to one row, and :func:`presence`, the pure function
that reduces a card's rows to a border. This module is the layer above them:
the three ways a row gets written, each of which is *that* plus the activity
trail.

* :func:`report` — a client said something. The PUT route and the agent
  socket both come through here, so the two transports cannot drift.
* :func:`end_open_sessions` — the process restarted. Nothing can be holding a
  socket, so nothing is running.
* :func:`reap_unwitnessed` — a row has gone quiet and no socket is vouching
  for it.

It sits apart from ``agent_sessions`` for an import reason as much as a
tidiness one: :mod:`app.services.activity` already imports that module, so
writing the trail from inside it would close a cycle.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now as clock_now
from app.models.activity import Channel
from app.models.agent_session import AgentSession, AgentSessionReason
from app.models.api_token import ApiToken
from app.models.task import Task
from app.schemas.agent_sessions import AgentSessionPut, AgentSessionRead
from app.services import activity, agent_sessions


class OpenRow(NamedTuple):
    """An open agent session, with the two facts closing it needs.

    Both are one join away from the session row and neither is on it, so they
    are fetched with it rather than looked up per row: the project to publish
    the change into, and the person whose token the agent is holding.
    """

    row: AgentSession
    project_id: UUID
    person_id: UUID | None


logger = logging.getLogger(__name__)


async def report(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    client_session_id: str,
    data: AgentSessionPut,
) -> AgentSessionRead:
    """Apply one report from a client, and write what changed to the trail.

    The single path. A heartbeat produces no transitions and therefore no
    trail entries, which is the whole reason ``upsert`` reports them
    separately rather than logging as it goes.
    """
    upserted = await agent_sessions.upsert(session, principal, task, client_session_id, data)
    for transition in upserted.transitions:
        await activity.record(
            session,
            principal,
            transition.verb,
            entity_type="task",
            entity_id=transition.task_id,
            project_id=transition.project_id,
            payload=transition.payload,
        )
    return agent_sessions.read(upserted.row)


async def end_open_sessions(
    session: AsyncSession,
    *,
    cause: str = "restart",
) -> int:
    """End every session still open, and say how many there were.

    Called once on startup. In a single-process deployment this is not a
    guess: if this process has only just begun, no socket is held by anyone,
    so no agent session is live — whatever the rows say. Without it a card
    left mid-turn by a restart pulses forever, because nothing computes
    staleness on read any more.

    A count above zero after a *clean* shutdown is worth looking at, which is
    why it is returned and logged rather than done quietly.
    """
    return await _end_each(session, await _open_rows(session), cause=cause)


async def end_session(
    session: AsyncSession,
    client_session_id: str,
    *,
    reason: AgentSessionReason = AgentSessionReason.CONNECTION_LOST,
    cause: str = "dropped",
) -> int:
    """End whatever one client session still holds open, wherever it is.

    Called when a socket closes. By client session and not by card, because
    the socket knows which conversation it was and the conversation knows
    which card — a session that walked the board mid-connection has exactly
    one open row, and this finds it without being told which.
    """
    rows = [
        entry
        for entry in await _open_rows(session)
        if entry.row.client_session_id == client_session_id
    ]
    return await _end_each(session, rows, cause=cause, reason=reason)


async def reap_unwitnessed(
    session: AsyncSession,
    live_session_ids: frozenset[str],
    *,
    quiet_after: timedelta,
) -> int:
    """End open rows that have gone quiet and hold no socket.

    The backstop for clients that report over HTTP and then die: a socket
    ending tells us immediately, a PUT stopping tells us nothing at all. A
    session in ``live_session_ids`` is witnessed however long it has been
    silent — an agent can sit on one tool call for an hour — so only the
    unwitnessed are considered.
    """
    cutoff = clock_now() - quiet_after
    rows = await _open_rows(session, quiet_before=cutoff)
    forgotten = [entry for entry in rows if entry.row.client_session_id not in live_session_ids]
    return await _end_each(session, forgotten, cause="unwitnessed")


async def _open_rows(
    session: AsyncSession,
    *,
    quiet_before: datetime | None = None,
) -> list[OpenRow]:
    """Every open session, with the project its card belongs to and whose it is.

    The person comes along on an outer join rather than a lookup per row: the
    trail entry this ends up writing has to name the same person every other
    entry about this agent named, and a reaper that quietly attributed them to
    nobody would leave a gap in one person's feed exactly where their agent
    went quiet. Outer because the token may since have been revoked and
    deleted, and a session whose owner cannot be named still has to be closed.
    """
    query = (
        select(AgentSession, Task.project_id, ApiToken.person_id)
        .join(Task, Task.id == AgentSession.task_id)
        .outerjoin(ApiToken, ApiToken.id == AgentSession.token_id)
        .where(AgentSession.ended_at.is_(None))
    )
    if quiet_before is not None:
        query = query.where(AgentSession.last_seen_at < quiet_before)
    return [
        OpenRow(row, project_id, person_id)
        for row, project_id, person_id in (await session.execute(query)).all()
    ]


async def _end_each(
    session: AsyncSession,
    rows: list[OpenRow],
    *,
    cause: str,
    reason: AgentSessionReason = AgentSessionReason.CONNECTION_LOST,
) -> int:
    """Close a set of rows, one trail entry each.

    Row by row rather than one bulk UPDATE, because the trail is the point:
    a card that went grey while nobody was watching should be able to say
    when, and why. There are never many — the open rows of a personal board.
    """
    if not rows:
        return 0

    moment = clock_now()
    for row, project_id, person_id in rows:
        transition = agent_sessions.close(row, reason, project_id, moment, cause=cause)
        await activity.record(
            session,
            _principal_of(row, person_id),
            transition.verb,
            entity_type="task",
            entity_id=transition.task_id,
            project_id=transition.project_id,
            payload=transition.payload,
        )

    logger.info(
        "Ended %d agent session(s) nobody said goodbye for",
        len(rows),
        extra={"context": {"cause": cause, "count": len(rows)}},
    )
    return len(rows)


def _principal_of(row: AgentSession, person_id: UUID | None) -> Principal:
    """Attribute the ending to the agent it happened to, not to the server.

    The trail reads "the agent's session ended", and the actor it names should
    be the agent — under the label the row was written with, which outlives
    the token that made it — acting for whoever's token it was. No scopes:
    nothing downstream of ``activity.record`` checks them, and inventing some
    would be claiming authority this has not been given.
    """
    return Principal(
        token_id=row.token_id,
        person_id=person_id,
        label=row.actor_label,
        scopes=frozenset(),
        channel=Channel.API,
    )
