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
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now as clock_now
from app.models.activity import Channel
from app.models.agent_session import AgentSession, AgentSessionReason
from app.models.task import Task
from app.schemas.agent_sessions import AgentSessionPut, AgentSessionRead
from app.services import activity, agent_sessions

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
    result = await agent_sessions.upsert(session, principal, task, client_session_id, data)
    for transition in result.transitions:
        await activity.record(
            session,
            principal,
            transition.verb,
            entity_type="task",
            entity_id=transition.task_id,
            project_id=transition.project_id,
            payload=transition.payload,
        )
    return agent_sessions.read(result.row, clock_now())


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
    forgotten = [
        (row, project_id)
        for row, project_id in rows
        if row.client_session_id not in live_session_ids
    ]
    return await _end_each(session, forgotten, cause="unwitnessed")


async def _open_rows(
    session: AsyncSession,
    *,
    quiet_before: datetime | None = None,
) -> list[tuple[AgentSession, UUID]]:
    """Every open session, with the project its card belongs to."""
    query = select(AgentSession, Task.project_id).join(Task, Task.id == AgentSession.task_id)
    query = query.where(AgentSession.ended_at.is_(None))
    if quiet_before is not None:
        query = query.where(AgentSession.last_seen_at < quiet_before)
    return [(row, project_id) for row, project_id in (await session.execute(query)).all()]


async def _end_each(
    session: AsyncSession,
    rows: list[tuple[AgentSession, UUID]],
    *,
    cause: str,
) -> int:
    """Close a set of rows as connection_lost, one trail entry each.

    Row by row rather than one bulk UPDATE, because the trail is the point:
    a card that went grey while nobody was watching should be able to say
    when, and why. There are never many — the open rows of a personal board.
    """
    if not rows:
        return 0

    moment = clock_now()
    for row, project_id in rows:
        transition = agent_sessions.close(
            row,
            AgentSessionReason.CONNECTION_LOST,
            project_id,
            moment,
            cause=cause,
        )
        await activity.record(
            session,
            _principal_of(row),
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


def _principal_of(row: AgentSession) -> Principal:
    """Attribute the ending to the agent it happened to, not to the server.

    The trail reads "the agent's session ended", and the actor it names should
    be the agent — under the label the row was written with, which outlives
    the token that made it. No scopes: nothing downstream of ``activity.record``
    checks them, and inventing some would be claiming authority this has not
    been given.
    """
    return Principal(
        token_id=row.token_id,
        label=row.actor_label,
        scopes=frozenset(),
        channel=Channel.API,
    )
