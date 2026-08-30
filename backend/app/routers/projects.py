"""Projects, and who is on them.

Every path here accepts either a project id or its key, so an agent can call
``/projects/ATL/members`` without first resolving a UUID.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import get_session
from app.models.person import PersonKind
from app.models.project import Project
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.schemas.projects import (
    Membership,
    MembershipUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectSummary,
    ProjectUpdate,
)
from app.services import activity, projects, vault

router = APIRouter(prefix="/projects", tags=["projects"])

ProjectRef = Path(
    description="The project's id, or its key such as `ATL` (case-insensitive).",
    examples=["ATL"],
)


async def resolved_project(
    project_ref: str = ProjectRef,
    session: AsyncSession = Depends(get_session),
) -> Project:
    """Turn the path segment into a project, 404-ing if nothing matches."""
    return await projects.resolve(session, project_ref)


def _read(project: Project) -> ProjectRead:
    return ProjectRead(
        id=project.id,
        key=project.key,
        name=project.name,
        description=project.description,
        colour=project.colour,
        archived_at=project.archived_at,
        created_at=project.created_at,
        member_count=len(project.members),
    )


@router.get("", response_model=list[ProjectRead], summary="List projects")
async def list_projects(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
    include_archived: bool = Query(default=False),
) -> list[ProjectRead]:
    """Return every project, newest first — the home screen's grid."""
    found = await projects.list_projects(session, include_archived=include_archived)
    return [_read(project) for project in found]


@router.post(
    "",
    response_model=ProjectRead,
    status_code=status.HTTP_201_CREATED,
    summary="Start a project",
    responses={409: {"description": "That key is already taken."}},
)
async def create_project(
    body: ProjectCreate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ProjectRead:
    """Create a project.

    The key becomes part of every task number on its board (`ATL-41`) and can
    be used in place of the id in any path here, so pick something short.
    """
    project = await projects.create(session, body)
    await activity.record(
        session,
        principal,
        "project.created",
        entity_type="project",
        entity_id=project.id,
        project_id=project.id,
        payload={"key": project.key, "name": project.name},
    )
    return _read(project)


@router.get("/{project_ref}", response_model=ProjectRead, summary="Get a project")
async def get_project(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
) -> ProjectRead:
    return _read(project)


@router.get(
    "/{project_ref}/summary",
    response_model=ProjectSummary,
    summary="Get a project's headline numbers",
)
async def get_summary(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
) -> ProjectSummary:
    """The counts behind the project hub's cards.

    Board and file figures join this response as those features land.
    """
    counts = await projects.member_counts(session, project)
    stored = await vault.counts(session, project)
    return ProjectSummary(
        **_read(project).model_dump(),
        team_count=counts[PersonKind.TEAM],
        client_count=counts[PersonKind.CLIENT],
        vault_tree_count=stored.trees,
        vault_secret_count=stored.secrets,
    )


@router.patch(
    "/{project_ref}",
    response_model=ProjectRead,
    summary="Update a project",
    responses={409: {"description": "That key is already taken."}},
)
async def update_project(
    body: ProjectUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ProjectRead:
    """Change any subset of a project's details. Omitted fields are left alone."""
    updated = await projects.update(session, project, body)
    await activity.record(
        session,
        principal,
        "project.updated",
        entity_type="project",
        entity_id=updated.id,
        project_id=updated.id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return _read(updated)


@router.delete("/{project_ref}", response_model=Acknowledged, summary="Archive a project")
async def archive_project(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Acknowledged:
    """Archive a project, hiding it from the grid.

    Nothing is deleted: its board, files and vault stay exactly as they were. A
    project holds credentials and uploads, so there is deliberately no way to
    destroy one through the API. Restore with `PATCH` and `{"archived": false}`.
    """
    await projects.archive(session, project)
    await activity.record(
        session,
        principal,
        "project.archived",
        entity_type="project",
        entity_id=project.id,
        project_id=project.id,
        payload={"key": project.key},
    )
    return Acknowledged()


@router.get("/{project_ref}/members", response_model=Membership, summary="List members")
async def list_members(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
) -> Membership:
    """Who is on this project — the pool the assignee picker draws from."""
    return Membership(members=[PersonRead.model_validate(p) for p in project.members])


@router.put(
    "/{project_ref}/members",
    response_model=Membership,
    summary="Replace the member list",
    responses={422: {"description": "An id is unknown, or names an archived person."}},
)
async def set_members(
    body: MembershipUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> Membership:
    """Set the project's membership to exactly these people.

    This replaces the list rather than adding to it: anyone not named is
    removed from the project. They stay in the directory, and anything they are
    already assigned keeps naming them.
    """
    people = await projects.set_members(session, project, body.person_ids)
    await activity.record(
        session,
        principal,
        "project.members_changed",
        entity_type="project",
        entity_id=project.id,
        project_id=project.id,
        payload={"member_count": len(people)},
    )
    return Membership(members=[PersonRead.model_validate(person) for person in people])
