"""Tick boxes on a card: the lightweight half of splitting one."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.card_deps import (
    WRITE_THIS_CARD,
    WRITE_THIS_CHECKLIST,
    resolved_checklist_item,
    resolved_task,
)
from app.api.presenters.tasks import checklist_item_read
from app.auth.principal import Principal
from app.db import SessionDependency
from app.models.activity import Verb
from app.models.task import Task, TaskChecklistItem
from app.schemas.common import Acknowledged
from app.schemas.tasks import ChecklistItemCreate, ChecklistItemRead, ChecklistItemUpdate
from app.services import tasks
from app.services.tasks import acts
from app.services.tasks.recording import record_on_card

router = APIRouter(tags=["tasks"])


@router.post(
    "/tasks/{task_ref}/checklist",
    response_model=ChecklistItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a checklist item",
)
async def add_checklist_item(
    body: ChecklistItemCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> ChecklistItemRead:
    """Add a tick-box sub-task to the bottom of a task's checklist.

    A line of text with no card, no owner and no reference — the lightweight
    half of sub-tasks. It still has to be ticked or cancelled before the card
    can reach the board's last column.
    """
    item = await tasks.add_checklist_item(session, task, body)
    await record_on_card(session, principal, Verb.TASK_CHECKLIST_ADDED, task, {"title": item.title})
    return checklist_item_read(item)


@router.patch(
    "/checklist/{item_id}",
    response_model=ChecklistItemRead,
    summary="Tick, cancel, reopen or retitle a checklist item",
)
async def update_checklist_item(
    body: ChecklistItemUpdate,
    item: TaskChecklistItem = Depends(resolved_checklist_item),
    principal: Principal = Depends(WRITE_THIS_CHECKLIST),
    session: AsyncSession = SessionDependency,
) -> ChecklistItemRead:
    """Change one tick box.

    `state` is `open`, `done` or `cancelled`; the last two both count as
    settled, so either one stops the item holding its card back.
    """
    updated = await acts.update_checklist_item(session, principal, item, body)
    return checklist_item_read(updated)


@router.delete(
    "/checklist/{item_id}", response_model=Acknowledged, summary="Delete a checklist item"
)
async def delete_checklist_item(
    item: TaskChecklistItem = Depends(resolved_checklist_item),
    principal: Principal = Depends(WRITE_THIS_CHECKLIST),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Remove a tick box entirely. Cancel it instead to keep the record."""
    task = await tasks.get(session, item.task_id)
    title = item.title
    await tasks.delete_checklist_item(session, item)
    await record_on_card(session, principal, Verb.TASK_CHECKLIST_DELETED, task, {"title": title})
    return Acknowledged()
