"""Finding tasks: by reference, by id, by board, and under a card."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute, defer

from app.auth.principal import Principal
from app.core.errors import NotFoundError, not_on_this_project
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.services import columns, projects, references
from app.services.paging import PageRequest, Window, keyset


async def resolve(session: AsyncSession, reference: str) -> Task:
    """Look a task up by id or by reference.

    Args:
        reference: A UUID, or a task reference such as ``ATL-41`` — or
            ``ATL-41-2`` for a sub-task (case-insensitive on the key).

    Raises:
        NotFoundError: if nothing matches.
    """
    try:
        task_id = UUID(reference)
    except ValueError:
        statement = _by_reference(reference)
    else:
        statement = select(Task).where(Task.id == task_id)

    task = await session.scalar(statement)
    if task is None:
        raise NotFoundError(f"No task matching {reference!r}.")
    return task


async def resolve_reachable(session: AsyncSession, principal: Principal, reference: str) -> Task:
    """A task by id or reference, if the caller may see its project.

    The one way a request or a socket message turns what it names into a card.
    A card on a project the caller is not a member of reads exactly like one
    that does not exist — and an agent token sees what the person who minted it
    sees.

    Raises:
        NotFoundError: if nothing matches, or the card is out of reach.
    """
    task = await resolve(session, reference)
    await projects.require_reachable(
        session, task.project_id, principal, NotFoundError(f"No task matching {reference!r}.")
    )
    return task


async def require_on_project(session: AsyncSession, project_id: UUID, reference: str) -> Task:
    """A task a request body names, which has to be on this project.

    The body's counterpart to ``resolved_task``, for a route whose path names
    the project and whose body names the card. Nonexistent and elsewhere are
    one answer, as for every reference that is not on the project
    (CYLIST-146) — and so a card on a board the caller cannot see reads
    exactly like one that does not exist.

    Raises:
        UnprocessableRequestError: from
            :func:`app.core.errors.not_on_this_project`.
    """
    try:
        task = await resolve(session, reference)
    except NotFoundError:
        raise not_on_this_project("task", task=reference) from None
    if task.project_id != project_id:
        raise not_on_this_project("task", task=reference)
    return task


def _by_reference(reference: str) -> Select[tuple[Task]]:
    """The query for the task a human reference such as ``ATL-41-2`` names.

    Raises:
        NotFoundError: if the reference is not shaped like one at all.
    """
    parsed = references.task_reference(reference)
    if parsed is None:
        if references.is_too_deep(reference):
            raise NotFoundError(
                f"No task matching {reference!r}. Sub-tasks go one level deep, "
                "so a reference carries at most two numbers."
            )
        raise NotFoundError(f"No task matching {reference!r}.")

    card = (
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(Project.key == parsed.key, Task.number == parsed.number)
    )
    if parsed.sub_number is None:
        return card

    parent = card.subquery()
    return select(Task).where(
        Task.parent_id.in_(select(parent.c.id)),
        Task.sub_number == parsed.sub_number,
    )


async def get(session: AsyncSession, task_id: UUID) -> Task:
    """One task by id, for a row that already holds the id rather than a reference.

    Raises:
        NotFoundError: if nothing matches.
    """
    task = await session.get(Task, task_id)
    if task is None:
        raise NotFoundError(f"No task matching {str(task_id)!r}.")
    return task


LISTED_WITHOUT = (defer(Task.description),)
"""Loader options for a list of cards. The description is the one unbounded
field on a card and no list shows it, so no list reads it either."""


@dataclass(frozen=True)
class ColumnStack:
    """The top of one column, and how many cards the whole column holds."""

    column: BoardColumn
    window: Window[Task]
    total: int


def _in_column(column_id: UUID) -> Select[tuple[Task]]:
    # Only top-level cards are ever in a column — a sub-task is not on the
    # board, and a card in cold storage has left it — so the column alone says
    # which cards are on it.
    return select(Task).where(Task.column_id == column_id)


def _stacking() -> tuple[InstrumentedAttribute[int | None], InstrumentedAttribute[UUID]]:
    # Top to bottom. ``position`` is a sort key with gaps, not an index, and
    # only its order is used here; the id comes last because a page continues
    # from a key that no other row shares.
    return Task.position, Task.id


async def board_pages(session: AsyncSession, project: Project, *, limit: int) -> list[ColumnStack]:
    """The top ``limit`` cards of every column on the board, left to right.

    Two queries for the whole board whatever its width: one ranks each card
    within its column and keeps the top of each, the other counts every
    column. Empty columns are in the answer too, so a board can be drawn from
    it alone.
    """
    board = await columns.list_for_project(session, project)
    if not board:
        return []
    ids = [column.id for column in board]
    position, task_id = _stacking()
    ranked = (
        select(
            Task.id,
            func.row_number()
            .over(partition_by=Task.column_id, order_by=(position, task_id))
            .label("rank"),
        )
        .where(Task.column_id.in_(ids))
        .subquery()
    )
    found = await session.scalars(
        select(Task)
        .join(ranked, ranked.c.id == Task.id)
        .where(ranked.c.rank <= limit + 1)
        .options(*LISTED_WITHOUT)
        .order_by(Task.column_id, position, task_id)
    )
    stacked: dict[UUID, list[Task]] = {column_id: [] for column_id in ids}
    for task in found:
        if task.column_id is not None:
            stacked[task.column_id].append(task)
    totals: dict[UUID, int] = {
        column_id: count
        for column_id, count in await session.execute(
            select(Task.column_id, func.count())
            .where(Task.column_id.in_(ids))
            .group_by(Task.column_id)
        )
        if column_id is not None
    }
    return [
        ColumnStack(
            column=column,
            window=Window(
                rows=stacked[column.id][:limit],
                next=stacked[column.id][limit - 1].id if len(stacked[column.id]) > limit else None,
            ),
            total=totals.get(column.id, 0),
        )
        for column in board
    ]


async def column_page(
    session: AsyncSession, column: BoardColumn, page: PageRequest
) -> Window[Task]:
    """One page of a column, top to bottom — what follows its first page."""
    return await keyset(
        session,
        _in_column(column.id),
        keys=_stacking(),
        page=page,
        options=LISTED_WITHOUT,
    )


async def all_for_project(session: AsyncSession, project: Project) -> list[Task]:
    """Every task on the project, sub-tasks among them, oldest first.

    For the reader that is not drawing a board: a day's report looks a card up
    by the reference an activity entry names, and ``ATL-41-2`` is as likely to
    be in a day's work as ``ATL-41`` is.
    """
    return list(
        await session.scalars(
            select(Task).where(Task.project_id == project.id).order_by(Task.created_at)
        )
    )


async def subtasks(session: AsyncSession, task: Task) -> list[Task]:
    """The sub-tasks with a reference of their own, in sub-number order.

    Not on the board — they belong to this card and are read here, or on it.
    """
    return list(
        await session.scalars(
            select(Task).where(Task.parent_id == task.id).order_by(Task.sub_number)
        )
    )


async def subtask_page(session: AsyncSession, task: Task, page: PageRequest) -> Window[Task]:
    """One page of :func:`subtasks`, in sub-number order."""
    return await keyset(
        session,
        select(Task).where(Task.parent_id == task.id),
        keys=(Task.sub_number, Task.id),
        page=page,
        options=LISTED_WITHOUT,
    )
