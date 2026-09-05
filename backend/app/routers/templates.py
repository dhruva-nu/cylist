"""Task templates: what kind of card a task is, and the rule for where its
cards go and what they owe each column."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.project import Project
from app.models.template import TaskTemplate
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.templates import (
    StageRead,
    TemplateCreate,
    TemplateRead,
    TemplateUpdate,
)
from app.services import activity, templates

router = APIRouter(tags=["templates"])


async def resolved_template(
    template_id: UUID,
    session: AsyncSession = SessionDependency,
) -> TaskTemplate:
    """Turn the path segment into a template, 404-ing if nothing matches."""
    return await templates.get_template(session, template_id)


def _template(template: TaskTemplate, task_count: int) -> TemplateRead:
    stages = sorted(template.stages, key=lambda stage: stage.column.position)
    return TemplateRead(
        id=template.id,
        project_id=template.project_id,
        name=template.name,
        description=template.description,
        stages=[
            StageRead(
                column_id=stage.column_id,
                column_name=stage.column.name,
                sub_stage_labels=stage.sub_stage_labels,
            )
            for stage in stages
        ],
        allowed_column_ids=[stage.column_id for stage in stages],
        task_count=task_count,
        created_at=template.created_at,
    )


async def _one_template(session: AsyncSession, template: TaskTemplate) -> TemplateRead:
    counts = await templates.template_task_counts(session, template.project_id)
    return _template(template, counts.get(template.id, 0))


@router.get(
    "/projects/{project_ref}/templates",
    response_model=list[TemplateRead],
    summary="List a project's task templates",
)
async def list_templates(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[TemplateRead]:
    """Every template on the project, in name order.

    `allowed_column_ids` is the answer a client needs before it offers a card
    a column. **Empty means unrestricted** — this template has no stages yet,
    so its cards go anywhere on the board.
    """
    found = await templates.list_templates(session, project)
    counts = await templates.template_task_counts(session, project.id)
    return [_template(template, counts.get(template.id, 0)) for template in found]


@router.post(
    "/projects/{project_ref}/templates",
    response_model=TemplateRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a task template",
    responses={
        409: {"description": "The project already has a template by that name."},
        422: {"description": "A stage names a column that is not this project's."},
    },
)
async def create_template(
    body: TemplateCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TemplateRead:
    """Add a kind of card to the project, with however many stages it starts
    with. A template with no stages is unrestricted until it gains one."""
    template = await templates.create_template(session, project, body)
    await activity.record(
        session,
        principal,
        "template.created",
        entity_type="template",
        entity_id=template.id,
        project_id=project.id,
        payload={"name": template.name, "stage_count": len(template.stages)},
    )
    return await _one_template(session, template)


@router.patch(
    "/templates/{template_id}",
    response_model=TemplateRead,
    summary="Edit a template",
    responses={
        409: {"description": "The project already has a template by that name."},
        422: {"description": "A stage names a column that is not this project's."},
    },
)
async def update_template(
    body: TemplateUpdate,
    template: TaskTemplate = Depends(resolved_template),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TemplateRead:
    """Rename a template, reword it, or replace its stages outright.

    `stages` is all-or-nothing: sending it replaces the whole set, because the
    stages together are the policy. Leave it out to change only the name or
    the description.
    """
    updated = await templates.update_template(session, template, body)
    await activity.record(
        session,
        principal,
        "template.updated",
        entity_type="template",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"name": updated.name, "fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return await _one_template(session, updated)


@router.delete(
    "/templates/{template_id}",
    response_model=Acknowledged,
    summary="Delete a template",
    responses={409: {"description": "Cards were created from it."}},
)
async def delete_template(
    template: TaskTemplate = Depends(resolved_template),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a template nothing was created from, and every stage about it.

    A template a card is using cannot be deleted: the card's template is what
    its column rule and its sub-stages are about, and taking it away would
    quietly free the card and drop what it still owes.
    """
    project_id, name = template.project_id, template.name
    await templates.delete_template(session, template)
    await activity.record(
        session,
        principal,
        "template.deleted",
        entity_type="template",
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()
