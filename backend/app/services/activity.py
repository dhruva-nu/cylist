"""Writing the audit trail.

Call :func:`record` from the service that performed the change, never from a
router — that way an action logged once is logged however it was triggered.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.activity import Activity


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
        actor_token_id=principal.token_id,
        actor_label=principal.label,
        channel=principal.channel,
        verb=verb,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        payload=payload or {},
    )
    session.add(entry)
    return entry
