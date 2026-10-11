"""A session's feed: what its agent read, edited, ran and said, line by line.

The presence daemon on the agent's machine follows the session's Claude Code
transcript and sends what it finds as ``feed`` frames on the socket it
already holds (see :mod:`app.realtime.protocol`). :func:`append` keeps them;
:func:`recent` reads a card's back.

**Kept, not only pushed past.** A feed that existed only on the wire would
show nothing to a person who opened the card halfway through a session, and
would empty itself on every reload — and a card is opened precisely to ask
"how did it get here?". So the lines are rows. They are trimmed to the newest
:data:`KEEP_PER_SESSION` of each session on every write, because this is a
window on the work and not an archive of it; the activity trail is still the
record of what happened to the card.

**Never history, always news.** A line is not an activity entry — a session
writes hundreds — and so it does not pass through
:func:`app.services.activity.record`. Instead it rings a doorbell of its own,
``agent.feed``, carrying the card it belongs to and nothing else; a browser
that has that card open refetches its feed, and every other browser ignores
it. The board itself does not refetch for one.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import publish_into
from app.models.agent_session import AgentFeedEntry, AgentSession
from app.models.task import Task
from app.realtime import protocol
from app.schemas.agent_sessions import AgentFeedEntryIn, AgentFeedEntryRead

KEEP_PER_SESSION = 200
"""How many lines each session keeps. Enough to scroll back through a long
turn; few enough that a card's feed is never a thing to paginate."""


async def append(
    session: AsyncSession,
    task: Task,
    client_session_id: str,
    entries: Sequence[AgentFeedEntryIn],
) -> int:
    """Keep what a session just did, and say so to whoever has the card open.

    Only an *open* row on this card takes lines. A daemon follows its
    transcript independently of the state it reports, so a frame can arrive
    for a session that has not been put on the card yet, or that has just
    walked off it; either way there is nothing on this card for the lines to
    belong to, and they are dropped rather than starting a row the hook never
    reported. Returns how many were kept.
    """
    if not entries:
        return 0
    row = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task.id,
            AgentSession.client_session_id == client_session_id,
            AgentSession.ended_at.is_(None),
        )
    )
    if row is None:
        return 0

    session.add_all(
        AgentFeedEntry(
            agent_session_id=row.id,
            task_id=task.id,
            at=entry.at,
            kind=entry.kind,
            tool=entry.tool,
            text=entry.text,
        )
        for entry in entries
    )
    await session.flush()
    await _trim(session, row.id)

    publish_into(
        session,
        {"type": protocol.FEED_CHANGED, "project_id": task.project_id, "task_id": task.id},
    )
    return len(entries)


async def _trim(session: AsyncSession, agent_session_id: UUID) -> None:
    """Drop all but the newest :data:`KEEP_PER_SESSION` lines of one session."""
    newest = (
        select(AgentFeedEntry.id)
        .where(AgentFeedEntry.agent_session_id == agent_session_id)
        .order_by(AgentFeedEntry.seq.desc())
        .limit(KEEP_PER_SESSION)
    )
    await session.execute(
        delete(AgentFeedEntry).where(
            AgentFeedEntry.agent_session_id == agent_session_id,
            AgentFeedEntry.id.not_in(newest),
        )
    )


async def recent(session: AsyncSession, task: Task, *, limit: int) -> list[AgentFeedEntryRead]:
    """A card's newest lines, newest first, from every session that was on it."""
    rows = await session.execute(
        select(AgentFeedEntry, AgentSession.client_name)
        .join(AgentSession, AgentSession.id == AgentFeedEntry.agent_session_id)
        .where(AgentFeedEntry.task_id == task.id)
        .order_by(AgentFeedEntry.seq.desc())
        .limit(limit)
    )
    return [
        AgentFeedEntryRead(
            id=entry.id,
            agent_session_id=entry.agent_session_id,
            client_name=client_name,
            at=entry.at,
            kind=entry.kind,
            tool=entry.tool,
            text=entry.text,
        )
        for entry, client_name in rows.all()
    ]
