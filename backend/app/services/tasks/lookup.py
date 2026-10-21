"""Finding tasks: by reference, by id, by board, and under a card."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, not_on_this_project
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.services import references


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


async def list_for_project(session: AsyncSession, project: Project) -> list[Task]:
    """Return the board's cards, in the order they are stacked.

    Top-level cards alone: a sub-task is not on the board, and arrives with the
    card it belongs to instead — see :func:`subtasks`. Anything that wants every
    row on the project, sub-tasks included, wants :func:`all_for_project`.
    """
    return list(
        await session.scalars(
            select(Task)
            .join(BoardColumn, BoardColumn.id == Task.column_id)
            .where(Task.project_id == project.id, Task.parent_id.is_(None))
            .order_by(BoardColumn.position, Task.position)
        )
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
