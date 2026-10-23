"""Three changes to a card that can be made from more than one place.

Finishing a sub-task, ticking a box and moving a card's stage are what the
board's own routes do — and what ticking a linked to-do on a day's plan does
too. Each is written here once, change and history line together, so the two
paths cannot drift: a to-do ticked is the card changed exactly as the board
would change it, and recorded the same way.

None of these checks who may: the caller does, with
:func:`app.routers.tasks.dependencies.may_write_card` or
:func:`~app.routers.tasks.dependencies.may_stage_card`, which are the rules
the board's guards apply.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.activity import Verb
from app.models.task import Task, TaskChecklistItem
from app.routers.tasks.recording import record_on_card
from app.schemas.tasks import ChecklistItemUpdate
from app.services import tasks


async def finish(
    session: AsyncSession, principal: Principal, task: Task, *, finished: bool
) -> Task:
    """Tick a sub-task off, or put it back, and say so on its history."""
    updated, changes = await tasks.set_finished(session, task, finished=finished)
    # Nothing changed means the tick box was already where the caller wants it,
    # and a history line for that would be a record of a click rather than of
    # the work.
    if changes:
        await record_on_card(
            session,
            principal,
            Verb.TASK_FINISHED,
            updated,
            {
                "parent": updated.parent.reference if updated.parent else None,
                "finished": finished,
                "changes": changes,
            },
        )
    return updated


async def update_checklist_item(
    session: AsyncSession,
    principal: Principal,
    item: TaskChecklistItem,
    body: ChecklistItemUpdate,
) -> TaskChecklistItem:
    """Change one tick box, and say so on its card's history."""
    updated = await tasks.update_checklist_item(session, item, body)
    task = await tasks.get(session, updated.task_id)
    await record_on_card(
        session,
        principal,
        Verb.TASK_CHECKLIST_UPDATED,
        task,
        {"title": updated.title, "state": updated.state.value},
    )
    return updated


async def move_stage(session: AsyncSession, principal: Principal, task: Task, index: int) -> Task:
    """Put a card on one of its stages, and say so on its history."""
    updated, changes = await tasks.set_sub_status(session, task, index)
    await record_on_card(
        session,
        principal,
        Verb.TASK_SUB_STATUS_MOVED,
        updated,
        {
            "sub_status_index": updated.sub_status_index,
            "sub_status": updated.sub_statuses[index],
            "changes": changes,
        },
    )
    return updated
