"""Handing out the numbers a task's reference is spelled with.

Both counters are locked rather than read as ``MAX(...) + 1``: a deleted
task's number must never come back, and two callers creating a task at the
same moment must not be handed the same one.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.project import Project
from app.models.task import Task


async def next_number(session: AsyncSession, project: Project) -> int:
    """Take the next task number for a project, from its locked counter."""
    counter = await session.scalar(
        select(Project.task_counter).where(Project.id == project.id).with_for_update()
    )
    project.task_counter = (counter or 0) + 1
    await session.flush()
    return project.task_counter


async def next_sub_number(session: AsyncSession, parent: Task) -> int:
    """Take the next sub-number under a card.

    The parent's own counter, locked, for the same reason a project has one:
    ``ATL-41-2`` must never name a second sub-task, however many were deleted
    or created at the same moment.
    """
    counter = await session.scalar(
        select(Task.subtask_counter).where(Task.id == parent.id).with_for_update()
    )
    parent.subtask_counter = (counter or 0) + 1
    await session.flush()
    return parent.subtask_counter
