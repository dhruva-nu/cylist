"""Tick boxes: the lightweight half of splitting a card.

A checklist item has no column, no owner and no reference — just a line of text
that has to be ticked or cancelled before its card can reach the end of the
board. Positions are contiguous from zero within a card.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.models.task import ChecklistState, Task, TaskChecklistItem
from app.schemas.tasks import ChecklistItemCreate, ChecklistItemUpdate


async def checklist(session: AsyncSession, task: Task) -> list[TaskChecklistItem]:
    """The task's tick boxes, top to bottom."""
    return await _items_of(session, task.id)


async def add_checklist_item(
    session: AsyncSession, task: Task, data: ChecklistItemCreate
) -> TaskChecklistItem:
    """Add a tick box to the bottom of a task's checklist."""
    existing = await session.scalar(
        select(func.count())
        .select_from(TaskChecklistItem)
        .where(TaskChecklistItem.task_id == task.id)
    )
    item = TaskChecklistItem(
        task_id=task.id,
        title=data.title,
        state=ChecklistState.OPEN,
        position=existing or 0,
    )
    session.add(item)
    await session.flush()
    return item


async def get_checklist_item(session: AsyncSession, item_id: UUID) -> TaskChecklistItem:
    """One tick box by id.

    Raises:
        NotFoundError: if nothing matches.
    """
    item = await session.get(TaskChecklistItem, item_id)
    if item is None:
        raise NotFoundError("No checklist item with that id.")
    return item


async def update_checklist_item(
    session: AsyncSession, item: TaskChecklistItem, data: ChecklistItemUpdate
) -> TaskChecklistItem:
    """Retitle an item, or tick, cancel or reopen it."""
    if data.title is not None:
        item.title = data.title
    if data.state is not None:
        item.state = data.state
    await session.flush()
    return item


async def delete_checklist_item(session: AsyncSession, item: TaskChecklistItem) -> None:
    """Remove a tick box, closing the gap it leaves."""
    task_id = item.task_id
    await session.delete(item)
    await session.flush()
    for position, remaining in enumerate(await _items_of(session, task_id)):
        remaining.position = position
    await session.flush()


async def _items_of(session: AsyncSession, task_id: UUID) -> list[TaskChecklistItem]:
    return list(
        await session.scalars(
            select(TaskChecklistItem)
            .where(TaskChecklistItem.task_id == task_id)
            .order_by(TaskChecklistItem.position)
        )
    )
