"""Writing the audit trail, and reading it back.

Every row carries both halves of "who": the credential, and the person behind
it. The credential answers *how* — this browser session, that agent's token —
and the person answers *who*, which is the half that survives the credential
being revoked and the half a reader actually wants.

Reading is the other half, and it is asked in three shapes. :func:`for_entity`
narrows the trail to one thing, which is how a card answers "what happened to
me"; :func:`between` narrows it to one project over a stretch of time, which is
how a day answers "what did I do"; and :func:`recent` is the raw feed, which
answers "who touched what". Turning a row into words is
:mod:`app.services.activity.wording`'s business rather than this module's.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.ids import uuid7
from app.models.activity import Activity
from app.services.paging import PageRequest, Window, keyset


@dataclass(frozen=True)
class Recorded:
    """What :func:`record` announces to its subscribers: who did it, and the row."""

    principal: Principal
    entry: Activity


Subscriber = Callable[[AsyncSession, Recorded], Awaitable[None]]
"""Something that wants to know of every change, inside the transaction that made it."""

_subscribers: list[Subscriber] = []


def subscribe(subscriber: Subscriber) -> None:
    """Have ``subscriber`` told of every entry recorded from now on.

    Registered once, by the app's factory (``create_app``), rather than found by
    this module: it used to import the agent sessions, the hooks and the
    realtime layer itself, which is why it could not be imported by any of them.
    A subscriber that is already registered is not registered twice, so
    building the app more than once is harmless. It runs in the recording
    transaction, in the order registered, and an exception it raises fails the
    write — a change that cannot be announced to hooks is not one to commit.
    """
    if subscriber not in _subscribers:
        _subscribers.append(subscriber)


async def record(
    session: AsyncSession,
    principal: Principal,
    verb: str,
    *,
    entity_type: str,
    entity_id: UUID | None = None,
    project_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Activity:
    """Append one entry to the audit trail.

    Writes the row and tells the subscribers, nothing more — see
    :func:`subscribe` for who they are.

    Args:
        verb: Dotted past-tense event name, e.g. ``"token.issued"``.
        entity_type: The kind of thing that changed, e.g. ``"task"``.
        entity_id: Which one, when the entity survives the change.
        project_id: Scopes the entry to a project's feed, when applicable.
        payload: Small, non-secret detail about the change. Never put a
            plaintext credential in here — the audit log is readable with the
            ``read`` scope alone.
    """
    entry = Activity(
        # Drawn now rather than at the flush: a hook delivery a subscriber
        # queues names this row, and has to be able to before either is written.
        id=uuid7(),
        actor_token_id=principal.token_id,
        actor_person_id=principal.person_id,
        actor_label=principal.label,
        channel=principal.channel,
        verb=verb,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        payload=payload or {},
    )
    session.add(entry)
    # Everything else a write means is for the subscribers, who say so once.
    recorded = Recorded(principal=principal, entry=entry)
    for subscriber in _subscribers:
        await subscriber(session, recorded)
    return entry


async def for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    *,
    limit: int,
    offset: int = 0,
) -> tuple[list[Activity], int]:
    """One page of one thing's own history, newest first, and how many there are.

    Newest first because a history is read to find out what just happened; the
    beginning of a long-running card is the part you already know. Paged
    because the record of a card worked on for a month is longer than anyone
    opening it wants at once, and the total comes back with the page so the
    caller can say how much more there is without asking again.

    An entry that recorded no change to the thing itself is left out: dragging
    a card up its own column, or saving a form without touching a field, is
    not something that happened to the work. Those rows are still in the audit
    feed, which is where "who touched this, and when" is asked; this is where
    "what is different about it now" is.
    """
    where = (
        Activity.entity_type == entity_type,
        Activity.entity_id == entity_id,
        changed_something(),
    )
    total = await session.scalar(select(func.count()).select_from(Activity).where(*where))
    entries = await session.scalars(
        select(Activity)
        .where(*where)
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(entries), total or 0


def changed_something() -> ColumnElement[bool]:
    """Whether an entry altered the thing it names, as a WHERE clause.

    Dragging a card up its own column, or saving a form without touching a
    field, is a request the server handled rather than something that happened
    to the work. Every reader that tells a story — a card's history, a day's
    report — leaves those out; the raw feed at ``/activity``, which answers
    "who touched what", keeps them.

    ``->`` gives SQL NULL for a payload with no ``changes`` at all, which
    coalesces to "something happened": an entry written before old and new
    values were recorded is not evidence that nothing changed.
    """
    return func.coalesce(func.jsonb_array_length(Activity.payload["changes"]), 1) > 0


async def between(
    session: AsyncSession,
    project_id: UUID,
    start: datetime,
    end: datetime,
) -> list[Activity]:
    """Everything that changed one project between two moments, oldest first.

    Half-open — ``start`` included, ``end`` excluded — so consecutive windows
    tile a week without an entry landing in two of them or in neither.

    Oldest first because this is read as a narrative rather than searched for
    the latest thing: a day is recounted in the order it happened.
    """
    entries = await session.scalars(
        select(Activity)
        .where(
            Activity.project_id == project_id,
            Activity.occurred_at >= start,
            Activity.occurred_at < end,
            changed_something(),
        )
        .order_by(Activity.occurred_at, Activity.id)
    )
    return list(entries)


async def recent(
    session: AsyncSession,
    page: PageRequest,
    *,
    project_id: UUID | None = None,
    within: list[UUID] | None = None,
    entity_type: str | None = None,
) -> Window[Activity]:
    """A page of the trail, newest first, optionally narrowed.

    ``within`` keeps the entries on those projects and the ones on no project
    at all, which is how a reader is shown only the boards they are on.

    Unlike the two readers above this keeps the entries that changed nothing:
    it is the feed that answers "who touched what", and a request somebody
    made is part of that answer whether or not it altered the work.
    """
    statement = select(Activity)
    if project_id is not None:
        statement = statement.where(Activity.project_id == project_id)
    if within is not None:
        statement = statement.where(
            or_(Activity.project_id.is_(None), Activity.project_id.in_(within))
        )
    if entity_type is not None:
        statement = statement.where(Activity.entity_type == entity_type)
    return await keyset(
        session, statement, keys=(Activity.occurred_at, Activity.id), page=page, descending=True
    )
