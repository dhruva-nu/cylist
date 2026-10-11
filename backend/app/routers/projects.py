"""Projects, and who is on them.

Every path here accepts either a project id or its key, so an agent can call
``/projects/ATL/members`` without first resolving a UUID.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import ForbiddenError, UnprocessableRequestError
from app.db import SessionDependency
from app.models.person import Person, PersonKind
from app.models.project import Project
from app.models.task import TaskStatus
from app.routers import guards
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.schemas.projects import (
    MemberRead,
    Membership,
    MembershipUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectSummary,
    ProjectUpdate,
)
from app.schemas.roles import RoleRead
from app.services import (
    activity,
    agents,
    columns,
    docs,
    files,
    goals,
    permissions,
    projects,
    roles,
    tasks,
    vault,
)
from app.storage import BlobStore, get_blob_store

router = APIRouter(prefix="/projects", tags=["projects"])

ProjectRef = Path(
    description="The project's id, or its key such as `ATL` (case-insensitive).",
    examples=["ATL"],
)


async def resolved_project(
    project_ref: str = ProjectRef,
    session: AsyncSession = SessionDependency,
) -> Project:
    """Turn the path segment into a project, 404-ing if nothing matches."""
    return await projects.resolve(session, project_ref)


def _project_read(project: Project) -> ProjectRead:
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


CHANGE_PROJECT = guards.on_project(Permission.PROJECT)
"""Renaming, rewording and archiving the project itself."""

CHANGE_MEMBERSHIP = guards.on_project(Permission.PEOPLE)
"""Saying who is on it. A different question from what the project is called,
and often a different person's job, so it is a permission of its own."""


Confirmation = Query(
    alias="confirm",
    description="The project's key, typed again, to confirm a delete that cannot be undone.",
    examples=["ATL"],
)


async def project_admin(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit only an admin of this project to destroying it.

    Deliberately not :data:`CHANGE_PROJECT`. `Permission.PROJECT` is renaming,
    rewording and archiving — three things somebody else can put back — and a
    role carrying it was given the right to tidy the project up, not to end it.
    Deleting one takes its vault and its uploads with it, and the only person
    on a board who can hand that right out is the only one who should hold it.

    Written out rather than built from :mod:`app.routers.guards`, because what
    it asks is not a permission: it is the question
    :func:`app.services.roles.may_administer` answers, the same one
    `/projects/{ref}/roles` asks before it lets anybody change a role.
    """
    if not await roles.may_administer(session, project, principal):
        raise ForbiddenError(
            f"Only an admin of {project.key} can delete it.",
            details={"project_key": project.key},
        )
    return principal


@router.get("", response_model=list[ProjectRead], summary="List projects")
async def list_projects(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    include_archived: bool = Query(default=False),
) -> list[ProjectRead]:
    """Return every project, newest first — the home screen's grid."""
    found = await projects.list_projects(session, include_archived=include_archived)
    return [_project_read(project) for project in found]


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
    session: AsyncSession = SessionDependency,
) -> ProjectRead:
    """Create a project.

    The key becomes part of every task number on its board (`ATL-41`) and can
    be used in place of the id in any path here, so pick something short.
    """
    project = await projects.create(session, body, creator_id=principal.person_id)
    await activity.record(
        session,
        principal,
        "project.created",
        entity_type="project",
        entity_id=project.id,
        project_id=project.id,
        payload={"key": project.key, "name": project.name},
    )
    return _project_read(project)


@router.get("/{project_ref}", response_model=ProjectRead, summary="Get a project")
async def get_project(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
) -> ProjectRead:
    return _project_read(project)


@router.get(
    "/{project_ref}/summary",
    response_model=ProjectSummary,
    summary="Get a project's headline numbers",
)
async def get_summary(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> ProjectSummary:
    """The counts behind the project hub's cards.

    One request behind all four cards on the project hub.

    The file and secret counts are of what *this reader* can reach. A count is
    a listing summed up, and one that said "11 files" to somebody who can only
    open ten would have told them a file exists — which is the one thing a
    level is for.
    """
    readable = await permissions.readable_levels(session, project, principal)
    people_counts = await projects.member_counts(session, project)
    task_counts = await tasks.status_counts(session, project)
    contents = await files.counts(session, project, readable=readable)
    stored = await vault.counts(session, project, readable=readable)
    agent_material = await agents.counts(session, project)
    goal_count, open_goals = await goals.counts_for_project(session, project)
    return ProjectSummary(
        **_project_read(project).model_dump(),
        team_count=people_counts[PersonKind.TEAM],
        client_count=people_counts[PersonKind.CLIENT],
        task_count=await tasks.card_count(session, project),
        column_count=await columns.count(session, project.id),
        blocked_count=task_counts[TaskStatus.BLOCKED],
        on_hold_count=task_counts[TaskStatus.HOLD],
        goal_count=goal_count,
        open_goal_count=open_goals,
        folder_count=contents.folders,
        file_count=contents.items,
        doc_count=await docs.count_for_project(session, project),
        vault_tree_count=stored.trees,
        vault_secret_count=stored.secrets,
        skill_count=agent_material.skills,
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
    principal: Principal = Depends(CHANGE_PROJECT),
    session: AsyncSession = SessionDependency,
) -> ProjectRead:
    """Change any subset of a project's details. Omitted fields are left alone.

    This is also how a project is archived and brought back — `{"archived":
    true}` takes it off the grid and leaves everything on it where it is,
    which `DELETE` emphatically does not.
    """
    updated = await projects.update(session, project, body)
    # Archiving is written as itself rather than as a field that changed. "Took
    # the project off the grid" and "renamed it" are not the same event to
    # anybody reading the log later, and `DELETE` stopped being the archive
    # route in CYLIST-73, so this is now the only place that says it happened.
    sent = body.model_dump(exclude_unset=True)
    described = sorted(field for field in sent if field != "archived")
    if described:
        await activity.record(
            session,
            principal,
            "project.updated",
            entity_type="project",
            entity_id=updated.id,
            project_id=updated.id,
            payload={"fields": described},
        )
    if body.archived is not None:
        await activity.record(
            session,
            principal,
            "project.archived" if body.archived else "project.restored",
            entity_type="project",
            entity_id=updated.id,
            project_id=updated.id,
            payload={"key": updated.key},
        )
    return _project_read(updated)


@router.delete(
    "/{project_ref}",
    response_model=Acknowledged,
    summary="Delete a project",
    responses={422: {"description": "`confirm` did not name this project's key."}},
)
async def delete_project(
    confirm: str = Confirmation,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
) -> Acknowledged:
    """Delete a project, and with it everything filed under it.

    Its board and every card on it, the folders and the uploaded bytes nothing
    else is holding, the docs, the vault and its secrets, the roles and who
    wore them. None of it comes back. Only the audit log survives, with the
    record of what happened on a project that is no longer there.

    Archiving is the reversible answer and is a different call: `PATCH` with
    `{"archived": true}` takes a project off the grid and leaves all of the
    above where it is.

    `confirm` has to be the project's own key. It is not ceremony — this path
    archived a project until CYLIST-73, so a caller written against the old
    meaning has to say something new before the new one runs, and gets a 422
    rather than a destroyed board.
    """
    if confirm.strip().upper() != project.key:
        raise UnprocessableRequestError(
            f"Name the project to delete it: confirm must be {project.key}.",
            details={"project_key": project.key},
        )

    key, name, project_id = project.key, project.name, project.id
    await projects.delete(session, store, project)
    await activity.record(
        session,
        principal,
        "project.deleted",
        entity_type="project",
        entity_id=project_id,
        # Not `project_id`: the row it would point at has just gone, and the
        # foreign key would null the column out from under this entry anyway.
        # The key and name are in the payload so the line still reads.
        project_id=None,
        payload={"key": key, "name": name},
    )
    return Acknowledged()


async def _membership(session: AsyncSession, project: Project, members: list[Person]) -> Membership:
    """Build the member list with each person's role on this project attached.

    The roles come from a query of their own because ``Project.members`` runs
    through ``secondary=`` and so never sees the join row the role sits on —
    see :func:`app.services.roles.member_roles`.
    """
    worn = await roles.member_roles(session, project)
    return Membership(
        members=[
            MemberRead(
                **PersonRead.model_validate(person).model_dump(),
                role=(RoleRead.model_validate(worn[person.id]) if person.id in worn else None),
            )
            for person in members
        ]
    )


@router.get("/{project_ref}/members", response_model=Membership, summary="List members")
async def list_members(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> Membership:
    """Who is on this project, and what each of them is on it.

    The pool the assignee picker draws from, and the one place a person's role
    is read from — `role` is null for anybody an admin has not said what they
    are yet.
    """
    return await _membership(session, project, list(project.members))


@router.put(
    "/{project_ref}/members",
    response_model=Membership,
    summary="Replace the member list",
    responses={422: {"description": "An id is unknown, or names an archived person."}},
)
async def set_members(
    body: MembershipUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(CHANGE_MEMBERSHIP),
    session: AsyncSession = SessionDependency,
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
    return await _membership(session, project, people)
