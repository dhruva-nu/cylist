"""Writing what happened to a card into the audit trail.

Nearly every write to a card ends the same way — an activity entry about one
card, naming it by reference — so the shape of that entry is written once.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.task import Task
from app.services import activity


async def record_on_card(
    session: AsyncSession,
    principal: Principal,
    verb: str,
    task: Task,
    payload: dict[str, Any] | None = None,
) -> None:
    """Record ``verb`` against ``task``, its reference first in the payload.

    The reference rides in every entry rather than being looked up through the
    id, because the card may be deleted and the history still has to say which
    one it was.
    """
    await activity.record(
        session,
        principal,
        verb,
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={"reference": task.reference, **(payload or {})},
    )
