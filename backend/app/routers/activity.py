"""The audit feed.

Answers "what changed, who changed it, and did a human or an agent do it?".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import get_session
from app.models.activity import Activity
from app.schemas.activity import ActivityRead
from app.services import projects

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityRead], summary="Recent activity")
async def list_activity(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
    project: str | None = Query(
        default=None,
        description="Limit to one project, by id or by key such as `ATL`.",
        examples=["ATL"],
    ),
    entity_type: str | None = Query(default=None, description="e.g. 'task', 'token'."),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[ActivityRead]:
    """Return audit entries, newest first.

    ``project`` takes a key as well as an id, matching every other
    project-scoped path — otherwise each client has to spend a request
    resolving the key before it can ask this one question.
    """
    statement = select(Activity).order_by(Activity.occurred_at.desc()).limit(limit)
    if project is not None:
        resolved = await projects.resolve(session, project)
        statement = statement.where(Activity.project_id == resolved.id)
    if entity_type is not None:
        statement = statement.where(Activity.entity_type == entity_type)

    entries = await session.scalars(statement)
    return [ActivityRead.model_validate(entry) for entry in entries]
