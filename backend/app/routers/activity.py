"""The audit feed.

Answers "what changed, who changed it, and did a human or an agent do it?".
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.schemas.activity import ActivityRead
from app.services import activity, projects

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("", response_model=list[ActivityRead], summary="Recent activity")
async def list_activity(
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
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

    Only the projects the caller is a member of are in it. Entries on no
    project at all — tokens, the people directory — are everybody's.
    """
    if project is not None:
        project_id = (await projects.resolve_reachable(session, project, principal)).id
        entries = await activity.recent(
            session, limit=limit, project_id=project_id, entity_type=entity_type
        )
    else:
        entries = await activity.recent(
            session,
            limit=limit,
            within=await projects.reachable_ids(session, principal),
            entity_type=entity_type,
        )
    return [ActivityRead.model_validate(entry) for entry in entries]
