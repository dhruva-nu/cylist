"""Roles: what somebody is on a project, and who says so.

Every path that names a role accepts either its id or its name, so an agent
can call `/projects/ATL/roles/Reviewer` with the word a human just read off a
badge.

Reading a project's roles needs nothing more than reading the project. Making
one, changing one, or putting one on somebody needs the project's admin — see
:func:`project_admin` below. That is the whole of what this change fences off;
what a role then lets its holder do is, deliberately, nothing at all.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import ForbiddenError
from app.db import SessionDependency
from app.models.person import Person
from app.models.project import Project
from app.models.role import ProjectRole
from app.routers.projects import resolved_project
from app.schemas.roles import (
    MemberRoleUpdate,
    RoleCreate,
    RoleRead,
    RoleSummary,
    RoleUpdate,
)
from app.services import activity, people, roles

router = APIRouter(tags=["roles"])

RoleRef = Path(
    description="The role's id, or its name such as `Reviewer` (case-insensitive).",
    examples=["Reviewer"],
)

PersonRef = Path(
    description="The person's id. People are addressed by id everywhere else too.",
)


async def resolved_person(
    person_ref: UUID = PersonRef,
    session: AsyncSession = SessionDependency,
) -> Person:
    """Turn the path segment into a directory entry, 404-ing if nothing matches."""
    return await people.get(session, person_ref)


async def resolved_role(
    role_ref: str = RoleRef,
    project: Project = Depends(resolved_project),
    session: AsyncSession = SessionDependency,
) -> ProjectRole:
    """Turn the path segment into one of this project's roles, or 404."""
    return await roles.resolve(session, project, role_ref)


async def project_admin(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit only a caller who may manage this project's roles.

    Two guards in one, and they answer different questions. ``Scope.WRITE``
    asks what the *credential* may do, so a read-only token is refused here as
    it is everywhere. The admin check asks who the *person* behind it is on
    this board — which is the question the ticket asks, and the one scopes
    have never been able to answer, since they say nothing about which project
    is in front of them.

    A credential acts as whoever minted it, so an agent token owned by an
    admin passes: that is the admin working through a program, and it is what
    ``api_token.person_id`` has meant since CYLIST-44. Keeping an agent out
    while letting its owner in is a job for a narrower scope on the token, not
    for this.
    """
    if not await roles.may_administer(session, project, principal):
        raise ForbiddenError(
            f"Only an admin of {project.key} can manage its roles.",
            details={"project_key": project.key},
        )
    return principal


def _read(role: ProjectRole) -> RoleRead:
    return RoleRead(
        id=role.id,
        name=role.name,
        description=role.description,
        colour=role.colour,
        is_admin=role.is_admin,
    )


def _summary(role: ProjectRole, member_count: int) -> RoleSummary:
    return RoleSummary(
        **_read(role).model_dump(),
        member_count=member_count,
        created_at=role.created_at,
    )


@router.get(
    "/projects/{project_ref}/roles",
    response_model=list[RoleSummary],
    summary="List a project's roles",
)
async def list_roles(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[RoleSummary]:
    """Every role on the project, the admin role first, then by name.

    Readable by anyone who can read the project: a badge nobody can look up is
    a badge nobody can read.
    """
    found = await roles.list_roles(session, project)
    counts = await roles.holder_counts(session, project)
    return [_summary(role, counts.get(role.id, 0)) for role in found]


@router.post(
    "/projects/{project_ref}/roles",
    response_model=RoleSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Add a role",
    responses={
        403: {"description": "You are not an admin of this project."},
        409: {"description": "This project already has a role by that name."},
    },
)
async def create_role(
    body: RoleCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RoleSummary:
    """Invent a role for this board.

    Omit `colour` and it takes a stable one from the palette — the same eight
    accents projects and people are drawn from, so a badge sits in the family
    of the avatar beside it.

    A new role starts with nobody on it. Put it on somebody with
    `PUT /projects/{ref}/members/{person}/role`.
    """
    role = await roles.create(session, project, body)
    await activity.record(
        session,
        principal,
        "role.created",
        entity_type="role",
        entity_id=role.id,
        project_id=project.id,
        payload={"name": role.name},
    )
    return _summary(role, 0)


@router.patch(
    "/projects/{project_ref}/roles/{role_ref}",
    response_model=RoleSummary,
    summary="Change a role",
    responses={
        403: {"description": "You are not an admin of this project."},
        409: {"description": "This project already has a role by that name."},
        422: {"description": "The admin role cannot be renamed."},
    },
)
async def update_role(
    body: RoleUpdate,
    project: Project = Depends(resolved_project),
    role: ProjectRole = Depends(resolved_role),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RoleSummary:
    """Rename a role, recolour it, or say what it means here.

    The admin role can be recoloured and described like any other, but not
    renamed: it is named in every explanation of who may do what.
    """
    before = role.name
    updated = await roles.update(session, role, body)
    counts = await roles.holder_counts(session, project)
    await activity.record(
        session,
        principal,
        "role.updated",
        entity_type="role",
        entity_id=updated.id,
        project_id=project.id,
        payload={
            "name": updated.name,
            "was": before if before != updated.name else None,
            "fields": sorted(body.model_dump(exclude_unset=True)),
        },
    )
    return _summary(updated, counts.get(updated.id, 0))


@router.delete(
    "/projects/{project_ref}/roles/{role_ref}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a role",
    responses={
        403: {"description": "You are not an admin of this project."},
        422: {"description": "Somebody still holds it, or it is the admin role."},
    },
)
async def delete_role(
    project: Project = Depends(resolved_project),
    role: ProjectRole = Depends(resolved_role),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> Response:
    """Remove a role from the project.

    Refused while anybody still holds it, naming them — a badge should not
    vanish off somebody's name because a list was tidied — and refused for the
    admin role, which is what makes any of this possible.
    """
    name = role.name
    role_id = role.id
    await roles.delete(session, project, role)
    await activity.record(
        session,
        principal,
        "role.deleted",
        entity_type="role",
        entity_id=role_id,
        project_id=project.id,
        payload={"name": name},
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put(
    "/projects/{project_ref}/members/{person_ref}/role",
    response_model=RoleRead | None,
    summary="Set a member's role",
    responses={
        403: {"description": "You are not an admin of this project."},
        404: {"description": "No such person, or no such role on this project."},
        422: {
            "description": ("They are not on this project, or this would leave it with no admin.")
        },
    },
)
async def set_member_role(
    body: MemberRoleUpdate,
    project: Project = Depends(resolved_project),
    person: Person = Depends(resolved_person),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RoleRead | None:
    """Say what somebody is on this board, or take their role off.

    Send `{"role": null}` to leave them a member with no role, which is what
    every member starts as. Refused if it would take the admin role off its
    last holder: a project nobody can administer has no way back.
    """
    role = await roles.resolve(session, project, body.role) if body.role else None
    await roles.set_member_role(session, project, person, role)
    await activity.record(
        session,
        principal,
        "member.role_set",
        entity_type="person",
        entity_id=person.id,
        project_id=project.id,
        payload={"person": person.name, "role": role.name if role else None},
    )
    return _read(role) if role else None
