"""Finding tasks: by reference, by id, by board, and under a card."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task


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


def _by_reference(reference: str) -> Select[tuple[Task]]:
    """The query for the task a human reference such as ``ATL-41-2`` names.

    Raises:
        NotFoundError: if the reference is not shaped like one at all.
    """
    # A key can hold no dash, so the parts are unambiguous: ``KEY-number``
    # names a card and ``KEY-number-sub`` names a sub-task of one.
    key, *numbers = reference.split("-")
    if not key or not numbers or not all(part.isdigit() for part in numbers):
        raise NotFoundError(f"No task matching {reference!r}.")
    if len(numbers) > 2:
        raise NotFoundError(
            f"No task matching {reference!r}. Sub-tasks go one level deep, "
            "so a reference carries at most two numbers."
        )

    card = (
        select(Task)
        .join(Project, Project.id == Task.project_id)
        .where(Project.key == key.upper(), Task.number == int(numbers[0]))
    )
    if len(numbers) == 1:
        return card

    parent = card.subquery()
    return select(Task).where(
        Task.parent_id.in_(select(parent.c.id)),
        Task.sub_number == int(numbers[1]),
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
