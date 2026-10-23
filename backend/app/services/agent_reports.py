"""Recording what an agent session is doing, however the news arrived.

:mod:`app.services.agent_sessions` owns the two hard parts — :func:`upsert`,
which applies one report to one row, and :func:`presence`, the pure function
that reduces a card's rows to a border. This module is the layer above them:
the three ways a row gets written, each of which is *that* plus the activity
trail.

* :func:`report` — a client said something. The PUT route and the agent
  socket both come through here, so the two transports cannot drift.
* :func:`end_session` — a socket closed.
* :func:`reap_unwitnessed` — a row has gone quiet and no socket is vouching
  for it.

There used to be a fourth, ending every open row on startup, on the grounds
that a process that has only just begun holds no sockets. That was true of
one process and false of two — a worker starting up ended the sessions its
peer was holding — and even with one it withdrew, on every deploy, every
question a person was about to answer (CYLIST-133, CYLIST-134). A session
whose socket died with the old process is now the reaper's, like any other
that has stopped being vouched for; and a daemon that is still alive has
reconnected, to whichever process, long before the reaper looks.

It sits apart from ``agent_sessions`` for an import reason as much as a
tidiness one: :mod:`app.services.activity` already imports that module, so
writing the trail from inside it would close a cycle.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now as clock_now
from app.db import publish_into
from app.models.agent_session import AgentSession, AgentSessionReason, SessionKey
from app.models.task import Task
from app.realtime.events import BoardChanged
from app.services import activity, agent_prompts, agent_sessions
from app.services.commands.agent_sessions import AgentSessionPut
from app.services.readmodels.agent_sessions import AgentSessionRead

logger = logging.getLogger(__name__)


REAPER_LEASE = 0x6379_6C69_7374_0001
"""The advisory lock a reaper pass holds: ``cyli`` ``st`` and a serial.

Every process runs a reaper, and every pass would close the same rows and
write the same trail lines twice over. Whichever process takes this lock for
its pass is the one that reaps; the others find it taken and skip that pass.
A transaction-scoped lock, so it is a lease that cannot outlive its holder: a
process that dies mid-pass lets go of it with its connection.
"""


class OpenRow(NamedTuple):
    """An open agent session, with the project closing it publishes into —
    one join away, so fetched with it rather than looked up per row."""

    row: AgentSession
    project_id: UUID


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

    A new note is the one change that is news without being history: the
    board is told — the card's agent line reads it — and the trail is not,
    because a line per tool call would bury everything else on it.
    """
    # A question the agent is holding its turn open for outranks "working"
    # from the same turn; an open permission is over once the session says
    # anything else.
    key = agent_sessions.key_for(principal, client_session_id)
    data = await agent_prompts.settle_for_report(session, task, key, data)
    upserted = await agent_sessions.upsert(session, principal, task, client_session_id, data)
    await agent_prompts.withdraw_for_sessions(session, upserted.ended, agent_prompts.SESSION_ENDED)
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
    if upserted.note_changed and not upserted.transitions:
        # A transition above has already rung the board's doorbell.
        publish_into(
            session,
            BoardChanged(
                project_id=task.project_id,
                entity_type="task",
                entity_id=task.id,
                verb="agent_session.noted",
            ),
        )
    return agent_sessions.read(upserted.row)


async def end_session(
    session: AsyncSession,
    key: SessionKey,
    *,
    reason: AgentSessionReason = AgentSessionReason.CONNECTION_LOST,
    cause: str = "dropped",
) -> int:
    """End whatever one client session still holds open, wherever it is.

    Called when a socket closes. By conversation and not by card, because
    the socket knows which conversation it was and the conversation knows
    which card — a session that walked the board mid-connection has exactly
    one open row, and this finds it without being told which.
    """
    query = _open_query().where(
        AgentSession.person_id == key.person_id,
        AgentSession.client_session_id == key.client_session_id,
    )
    return await _end_each(session, await _fetch(session, query), cause=cause, reason=reason)


async def take_reaper_lease(session: AsyncSession) -> bool:
    """Whether this transaction is the one to reap, this pass — see
    :data:`REAPER_LEASE`. Held until the transaction ends."""
    taken = await session.scalar(select(func.pg_try_advisory_xact_lock(REAPER_LEASE)))
    return bool(taken)


async def reap_unwitnessed(
    session: AsyncSession,
    *,
    quiet_after: timedelta,
    witnessed_for: timedelta,
) -> int:
    """End open rows that have gone quiet and that no socket is vouching for.

    The backstop for clients that report over HTTP and then die: a socket
    ending tells us immediately, a PUT stopping tells us nothing at all. And,
    since there is no sweep on startup, for the sessions whose socket went
    down with a process that never got to close it.

    A session whose socket stamped it within ``witnessed_for`` is witnessed
    however long it has been silent — an agent can sit on one tool call for
    an hour — so only the unwitnessed are considered. The stamp is in the
    row, not in this process's memory (CYLIST-133): the socket may be held by
    any process, and this may be any process's reaper.
    """
    moment = clock_now()
    query = _open_query().where(
        AgentSession.last_seen_at < moment - quiet_after,
        or_(
            AgentSession.socket_seen_at.is_(None),
            AgentSession.socket_seen_at < moment - witnessed_for,
        ),
    )
    return await _end_each(session, await _fetch(session, query), cause="unwitnessed")


def _open_query() -> Select[tuple[AgentSession, UUID]]:
    """Every open session, with the project its card belongs to — to be
    narrowed in the query rather than after it, because this is read on
    every socket that closes and on every reaper pass."""
    return (
        select(AgentSession, Task.project_id)
        .join(Task, Task.id == AgentSession.task_id)
        .where(AgentSession.ended_at.is_(None))
    )


async def _fetch(session: AsyncSession, query: Select[tuple[AgentSession, UUID]]) -> list[OpenRow]:
    return [OpenRow(row, project_id) for row, project_id in (await session.execute(query)).all()]


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
    await agent_prompts.withdraw_for_sessions(
        session, [entry.row for entry in rows], agent_prompts.SESSION_ENDED
    )
    for row, project_id in rows:
        transition = agent_sessions.close(row, reason, project_id, moment, cause=cause)
        await activity.record(
            session,
            agent_sessions.acting_principal(row),
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
