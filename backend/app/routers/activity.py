"""The audit feed.

Answers "what changed, who changed it, and did a human or an agent do it?".
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import get_session
from app.models.activity import Activity
from app.schemas.activity import ActivityRead

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityRead], summary="Recent activity")
async def list_activity(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
    project_id: UUID | None = Query(default=None, description="Limit to one project."),
    entity_type: str | None = Query(default=None, description="e.g. 'task', 'token'."),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ActivityRead]:
    """Return audit entries, newest first."""
    statement = select(Activity).order_by(Activity.occurred_at.desc()).limit(limit)
    if project_id is not None:
        statement = statement.where(Activity.project_id == project_id)
    if entity_type is not None:
        statement = statement.where(Activity.entity_type == entity_type)

    entries = await session.scalars(statement)
    return [ActivityRead.model_validate(entry) for entry in entries]
