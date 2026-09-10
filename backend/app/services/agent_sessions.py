"""Agent sessions: writing what a harness hook reports, and reading it back as a border.

Two halves. :func:`upsert` is what a Claude Code lifecycle hook calls on every
prompt, tool call and stop — it has to be cheap, idempotent, and to write the
activity trail only when something actually changed, because a heartbeat per
tool call would drown the feed the trail exists to be. :func:`presence` is the
pure function that turns a card's rows into the one state its border shows,
and is the part most worth testing on its own.

Between them sit two small hooks into the rest of the system.
:func:`touch_from_activity` is called from :func:`app.services.activity.record`,
the one choke point every mutation passes through, so an agent that is
actually writing to its card counts as heard from without a second request,
and a *person* touching the card in the browser clears the finished sessions
off it — which is the agreed way a green border goes away without a button.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.config import AGENT_SESSION_STALE_AFTER
from app.core.clock import now as clock_now
from app.core.errors import ForbiddenError
from app.models.activity import Channel
from app.models.agent_session import AgentSession, AgentSessionReason, AgentSessionState
from app.models.task import Task
from app.schemas.agent_sessions import (
    AgentPresence,
    AgentSessionPut,
    AgentSessionRead,
    PresenceState,
)


@dataclass(frozen=True)
class Transition:
    """One change of state worth a line in the activity trail."""

    verb: str
    task_id: UUID
    project_id: UUID
    payload: dict[str, Any]


@dataclass
class Upserted:
    """What one hook report did: the row as it stands, and what changed."""

    row: AgentSession
    transitions: list[Transition] = field(default_factory=list)


async def upsert(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    client_session_id: str,
    data: AgentSessionPut,
) -> Upserted:
    """Record one hook report against a card.

    Refused for a browser session: the web UI is where people are, and a
    person is not an agent whatever they type. Only a bearer token — the CLI,
    an MCP server, a hook — may say a session is working.

    A client session is on one card at a time. If this one has an open row on
    a *different* card, that row is ended with ``reason=moved`` first, so a
    ``/work`` on a second card, or a cylist write to one, walks the session
    across the board rather than cloning it.

    The row itself is found or created, then the state applied. ``working``
    clears the reason and, on a row that had finished, reopens it — a resumed
    conversation comes back to life on the row it left, with its dismissal
    undone — and every report refreshes ``last_seen_at``. Only a change of
    state is returned as a transition; a heartbeat returns none.
    """
    if principal.channel is not Channel.API:
        raise ForbiddenError(
            "Only an agent's API token can report a session on a card; the web UI cannot."
        )

    now = clock_now()
    transitions: list[Transition] = []

    # --- One card at a time: walk off any other card first ------------------
    elsewhere = await session.scalars(
        select(AgentSession).where(
            AgentSession.client_session_id == client_session_id,
            AgentSession.task_id != task.id,
            AgentSession.ended_at.is_(None),
        )
    )
    for other in elsewhere:
        _finish(other, AgentSessionReason.MOVED, now)
        other_task = await session.get(Task, other.task_id)
        if other_task is not None:
            transitions.append(
                Transition(
                    "agent_session.finished",
                    other_task.id,
                    other_task.project_id,
                    _payload(other, moved_to=task.reference),
                )
            )

    # --- Find or create --------------------------------------------------------
    row = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task.id,
            AgentSession.client_session_id == client_session_id,
        )
    )
    created = row is None
    if row is None:
        row = AgentSession(
            task_id=task.id,
            token_id=principal.token_id,
            actor_label=principal.label,
            client_session_id=client_session_id,
            state=data.state,
            reason=None,
            started_at=now,
            state_changed_at=now,
            last_seen_at=now,
        )
        session.add(row)

    was = None if created else row.state
    was_open = row.ended_at is None
    if data.client_name is not None:
        row.client_name = data.client_name
    row.last_seen_at = now

    # --- Apply the state -----------------------------------------------------
    if data.state is AgentSessionState.WORKING:
        row.state = AgentSessionState.WORKING
        row.reason = None
        row.ended_at = None
        row.dismissed_at = None
    elif data.state is AgentSessionState.WAITING:
        row.state = AgentSessionState.WAITING
        row.reason = data.reason or AgentSessionReason.TURN_ENDED
        row.ended_at = None
        row.dismissed_at = None
    else:
        _finish(row, data.reason or AgentSessionReason.SESSION_ENDED, now)

    changed = created or row.state is not was or (not was_open and row.ended_at is None)
    if changed:
        row.state_changed_at = now
        verb = _verb(row.state, fresh=created or not was_open)
        if verb is not None:
            transitions.append(Transition(verb, task.id, task.project_id, _payload(row)))

    # Flushed here rather than left to the commit, so a `touch_from_activity`
    # in the same request — the router records these transitions next — sees
    # the row it is about to refresh.
    await session.flush()
    return Upserted(row=row, transitions=transitions)


def _finish(row: AgentSession, reason: AgentSessionReason, now: datetime) -> None:
    if row.ended_at is None:
        row.state_changed_at = now
    row.state = AgentSessionState.DONE
    row.reason = reason
    row.ended_at = row.ended_at or now
    row.last_seen_at = now


def close(
    row: AgentSession,
    reason: AgentSessionReason,
    project_id: UUID,
    now: datetime,
    *,
    cause: str | None = None,
) -> Transition:
    """End one row from outside a hook report, and say so in the trail.

    For the endings nobody reported: a socket that dropped, a session too
    quiet to still believe in, a process that restarted while agents were
    running. :func:`upsert` is the wrong door for those — it exists to apply
    what a *client* said, and walks the session off any other card on the way
    — where this only closes the row in front of it.

    ``cause`` is the detail the reason deliberately does not carry: one
    ``connection_lost`` covers both a drop and an idle timeout, and this is
    where the difference is written down for whoever comes looking.
    """
    _finish(row, reason, now)
    return Transition(
        "agent_session.finished",
        row.task_id,
        project_id,
        _payload(row, cause=cause),
    )


def _verb(state: AgentSessionState, *, fresh: bool) -> str | None:
    """Which line the trail gets for a change of state, if any.

    Entering ``working`` is worth a line when the session is new or came back
    from finished; a turn starting after the last one ended is not — that is
    the rhythm of a conversation, and the trail would be nothing but it.
    """
    if state is AgentSessionState.WORKING:
        return "agent_session.started" if fresh else None
    if state is AgentSessionState.WAITING:
        return "agent_session.waiting"
    return "agent_session.finished"


def _payload(
    row: AgentSession, *, moved_to: str | None = None, cause: str | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "client_session_id": row.client_session_id,
        "client_name": row.client_name,
        "reason": row.reason.value if row.reason else None,
    }
    if moved_to is not None:
        payload["moved_to"] = moved_to
    if cause is not None:
        payload["cause"] = cause
    return payload


async def dismiss(session: AsyncSession, task: Task) -> int:
    """Clear every finished session off a card. Returns how many were cleared.

    Open sessions are left alone: a person cannot dismiss an agent that is
    still typing, only stop looking at ones that have stopped.
    """
    now = clock_now()
    result = await session.execute(
        update(AgentSession)
        .where(
            AgentSession.task_id == task.id,
            AgentSession.ended_at.is_not(None),
            AgentSession.dismissed_at.is_(None),
        )
        .values(dismissed_at=now)
        .returning(AgentSession.id)
    )
    return len(result.all())


async def touch_from_activity(
    session: AsyncSession,
    principal: Principal,
    verb: str,
    *,
    entity_type: str,
    entity_id: UUID | None,
) -> None:
    """What a write to a card means for the sessions on it.

    Called from :func:`app.services.activity.record` for every mutation, so it
    is kept to one UPDATE and no reads. An agent's own write to its card is a
    heartbeat — real work is better evidence of life than a hook — and a
    person's write from the browser dismisses whatever has finished there,
    which is the agreed rule for how a green border clears itself.

    The trail's own ``agent_session.*`` lines are skipped: the row they
    describe has just been written by the same request.
    """
    if entity_type != "task" or entity_id is None or verb.startswith("agent_session."):
        return

    now = clock_now()
    if principal.channel is Channel.API:
        await session.execute(
            update(AgentSession)
            .where(
                AgentSession.task_id == entity_id,
                AgentSession.token_id == principal.token_id,
                AgentSession.ended_at.is_(None),
            )
            .values(last_seen_at=now)
        )
    else:
        await session.execute(
            update(AgentSession)
            .where(
                AgentSession.task_id == entity_id,
                AgentSession.ended_at.is_not(None),
                AgentSession.dismissed_at.is_(None),
            )
            .values(dismissed_at=now)
        )


# --- Reading ---------------------------------------------------------------


async def list_for_task(session: AsyncSession, task: Task) -> list[AgentSession]:
    """A card's sessions worth showing: open first, then finished-undismissed."""
    return (await for_tasks(session, [task.id])).get(task.id, [])


async def for_tasks(
    session: AsyncSession, task_ids: Sequence[UUID]
) -> dict[UUID, list[AgentSession]]:
    """The undismissed sessions of a set of cards, one query for all of them.

    Open rows first, newest activity first within each group, so the first
    row of a card's list is the one most worth reading.
    """
    if not task_ids:
        return {}
    rows = await session.scalars(
        select(AgentSession)
        .where(
            AgentSession.task_id.in_(task_ids),
            AgentSession.dismissed_at.is_(None),
        )
        .order_by(
            AgentSession.ended_at.is_not(None),
            AgentSession.last_seen_at.desc(),
        )
    )
    grouped: dict[UUID, list[AgentSession]] = {}
    for row in rows:
        grouped.setdefault(row.task_id, []).append(row)
    return grouped


def is_stale(row: AgentSession, now: datetime, stale_after: timedelta) -> bool:
    """A working session nobody has heard from for too long."""
    return (
        row.state is AgentSessionState.WORKING
        and row.ended_at is None
        and now - row.last_seen_at > stale_after
    )


def read(
    row: AgentSession, now: datetime, stale_after: timedelta = AGENT_SESSION_STALE_AFTER
) -> AgentSessionRead:
    return AgentSessionRead(
        id=row.id,
        task_id=row.task_id,
        actor_label=row.actor_label,
        client_session_id=row.client_session_id,
        client_name=row.client_name,
        state=row.state,
        reason=row.reason,
        note=row.note,
        started_at=row.started_at,
        state_changed_at=row.state_changed_at,
        last_seen_at=row.last_seen_at,
        ended_at=row.ended_at,
        dismissed_at=row.dismissed_at,
        is_stale=is_stale(row, now, stale_after),
    )


def presence(
    rows: Iterable[AgentSession],
    now: datetime,
    stale_after: timedelta = AGENT_SESSION_STALE_AFTER,
) -> AgentPresence | None:
    """The one state a card's border shows, from every session on it.

    Whatever needs a human wins, then whatever is alive, then what is over:

    1. ``waiting`` if any open session is waiting.
    2. else ``working`` if any open session is working and not stale.
    3. else ``done`` if every session has ended (and at least one is still
       undismissed — dismissed rows are not passed in).
    4. else ``stale`` — the only open sessions are working but silent.
    5. else nothing: no border.

    A stale row is ignored while another row is live, so one crashed
    terminal does not dim a card somebody else is still working on.

    Pure, and tested on its own. ``rows`` is expected to be the undismissed
    sessions of one card, as :func:`for_tasks` returns them.
    """
    considered = [row for row in rows if row.dismissed_at is None]
    if not considered:
        return None

    open_rows = [row for row in considered if row.ended_at is None]
    waiting = [row for row in open_rows if row.state is AgentSessionState.WAITING]
    working = [
        row
        for row in open_rows
        if row.state is AgentSessionState.WORKING and not is_stale(row, now, stale_after)
    ]

    if waiting:
        return _summary("waiting", _latest(waiting), len(open_rows))
    if working:
        return _summary("working", _latest(working), len(open_rows))
    if not open_rows:
        return _summary("done", _latest(considered), len(considered))
    return _summary("stale", _latest(open_rows), len(open_rows))


def _latest(rows: list[AgentSession]) -> AgentSession:
    """The most recently heard-from row: the deciding session."""
    return max(rows, key=lambda row: row.last_seen_at)


def _summary(state: PresenceState, deciding: AgentSession, count: int) -> AgentPresence:
    return AgentPresence(
        state=state,
        count=count,
        reason=deciding.reason,
        client_name=deciding.client_name,
        since=deciding.state_changed_at,
        last_seen_at=deciding.last_seen_at,
    )
