"""The numbers drawn beside cards and on a project's hub.

Every function here answers for a whole set of cards in a fixed number of
queries, because these numbers appear on every task in every listing and a walk
per card would make the board's cost grow with its size.
"""

from __future__ import annotations

from typing import NamedTuple
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.person import Person
from app.models.project import Project
from app.models.task import ChecklistState, Task, TaskChecklistItem, TaskStatus


class Split(NamedTuple):
    """How far a card is through the things it was split into.

    Read as "``open`` of ``total`` left" — or, on a card, as the bar drawn from
    the two. Both halves count sub-tasks and tick boxes together, and neither
    counts anything cancelled.
    """

    total: int
    open: int


NOT_SPLIT = Split(total=0, open=0)
"""What a card that was never split reports: nothing under it, nothing open."""


async def status_counts(session: AsyncSession, project: Project) -> dict[TaskStatus, int]:
    """Count a project's tasks by status in one query — the hub's headline.

    Sub-tasks are counted. They are not cards and the board does not draw them,
    but a blocked sub-task is genuinely blocked work, and a hub reporting no
    blockages while somebody is waiting on one would be the hub at its least
    useful. How many *cards* there are is :func:`card_count`, which is the
    number that has to agree with the columns.
    """
    rows = await session.execute(
        select(Task.status, func.count()).where(Task.project_id == project.id).group_by(Task.status)
    )
    counts = dict.fromkeys(TaskStatus, 0)
    for status, total in rows.tuples():
        counts[status] = total
    return counts


async def card_count(session: AsyncSession, project: Project) -> int:
    """How many cards are on the board — sub-tasks not among them.

    The hub's "tasks" number, and it has to be the columns' counts added up: two
    places saying how big a board is and disagreeing about it is worse than
    either of them being the wrong number.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project.id, Task.parent_id.is_(None))
    )
    return total or 0


async def subtask_counts(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, Split]:
    """How many sub-tasks each card has, and how many are still open.

    Cards and tick boxes counted together, because the rule they answer to is
    one rule: while anything under a card is unsettled it cannot reach the last
    column, and it makes no difference to that whether the outstanding thing has
    a reference of its own.

    Two queries for the whole board rather than a walk per card. Cancelled
    items are out of both halves rather than counted as done: a card that was
    split into three and had one dropped has two sub-tasks, not three with one
    of them mysteriously complete.
    """
    if not task_ids:
        return {}

    sub_cards = await session.execute(
        select(
            Task.parent_id,
            func.count(),
            func.count().filter(Task.finished_at.is_(None)),
        )
        .where(Task.parent_id.in_(task_ids), Task.status != TaskStatus.CANCELLED)
        .group_by(Task.parent_id)
    )
    tick_boxes = await session.execute(
        select(
            TaskChecklistItem.task_id,
            func.count(),
            func.count().filter(TaskChecklistItem.state == ChecklistState.OPEN),
        )
        .where(
            TaskChecklistItem.task_id.in_(task_ids),
            TaskChecklistItem.state != ChecklistState.CANCELLED,
        )
        .group_by(TaskChecklistItem.task_id)
    )

    counts: dict[UUID, Split] = {}
    for task_id, total, still_open in [*sub_cards.tuples(), *tick_boxes.tuples()]:
        # `parent_id` is nullable on the model, but the IN above only matches
        # rows that have one.
        if task_id is None:
            continue
        running = counts.get(task_id, NOT_SPLIT)
        counts[task_id] = Split(running.total + total, running.open + still_open)
    return counts


async def subtask_owners(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, list[Person]]:
    """Who is on each card's sub-tasks, by name, in one query.

    The board no longer shows *where* a sub-task is, so this is what it shows
    instead: the faces on a card are everyone who owes it something, not just
    whoever owns the card itself. Cancelled sub-tasks are left out — nobody is
    on the hook for work that was dropped.
    """
    if not task_ids:
        return {}

    rows = await session.execute(
        select(Task.parent_id, Person)
        .join(Person, Person.id == Task.assignee_id)
        .where(Task.parent_id.in_(task_ids), Task.status != TaskStatus.CANCELLED)
        .order_by(Task.sub_number)
    )

    owners: dict[UUID, list[Person]] = {}
    seen: dict[UUID, set[UUID]] = {}
    for parent_id, person in rows.tuples():
        if parent_id is None:
            continue
        # In sub-number order and deduplicated, so one person owning three of
        # the sub-tasks is one face on the card rather than three of the same.
        if person.id in seen.setdefault(parent_id, set()):
            continue
        seen[parent_id].add(person.id)
        owners.setdefault(parent_id, []).append(person)
    return owners
