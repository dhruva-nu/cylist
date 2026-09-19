"""What each role on a project may do, and where an admin says so.

One screen reads from here and writes back to it: the grid of roles down the
side and permissions across the top. So one GET answers the whole thing —
which roles there are, who holds them, what each allows, what the reader
themselves may do — rather than making a page assemble it from four calls and
hope they agree with each other.

Writes are one row of the grid at a time and carry the whole row. See
:class:`~app.schemas.permissions.PermissionsUpdate` for why a delta would be
the wrong shape.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import CATALOGUE, Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import UnprocessableRequestError
from app.db import SessionDependency
from app.models.permission import EVERYONE_ELSE
from app.models.project import Project
from app.models.role import ProjectRole
from app.routers.projects import resolved_project
from app.routers.roles import project_admin, resolved_role
from app.schemas.permissions import (
    PermissionInfoRead,
    PermissionsUpdate,
    ProjectPermissions,
    RolePermissions,
)
from app.services import activity, permissions, roles

router = APIRouter(tags=["roles"])

EVERYONE_ELSE_NAME = "Everyone else"
"""What the baseline line is called on screen.

Not "Member", and not a seeded role: CYLIST-45 decided that a project's
role-less people are people nobody has described yet, and giving that state a
role's name would have been the seeded default it turned down. It is a line on
this grid because they still have to be able to do something, and what that is
is a question for the admin.
"""

EVERYONE_ELSE_COLOUR = "#8A8580"
"""Grey, and not from the palette every role and person is coloured from.
The baseline is not somebody — it is the absence of anybody having said — and
a badge in the accent family would read as one more role to hand out."""


def _catalogue() -> list[PermissionInfoRead]:
    return [
        PermissionInfoRead(key=entry.key, label=entry.label, summary=entry.summary)
        for entry in CATALOGUE
    ]


@router.get(
    "/projects/{project_ref}/permissions",
    response_model=ProjectPermissions,
    summary="What each role on a project may do",
)
async def get_permissions(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> ProjectPermissions:
    """The whole grid, and what the reader themselves may do on this board.

    Readable by anyone who can read the project, not only its admin. A client
    that cannot see this cannot tell an action it is not allowed from one that
    is simply broken, and would have to offer every button and let half of
    them refuse.
    """
    found = await roles.list_roles(session, project)
    counts = await roles.holder_counts(session, project)
    granted = await permissions.for_project(session, project)
    members = len(project.members)

    lines = [
        RolePermissions(
            role_id=role.id,
            name=role.name,
            colour=role.colour,
            is_admin=role.is_admin,
            member_count=counts.get(role.id, 0),
            # An admin's line is what it is rather than what is stored, and
            # nothing is stored: see `app.models.permission`.
            permissions=sorted(Permission) if role.is_admin else sorted(granted.get(role.id, ())),
        )
        for role in found
    ]
    lines.append(
        RolePermissions(
            role_id=EVERYONE_ELSE,
            name=EVERYONE_ELSE_NAME,
            colour=EVERYONE_ELSE_COLOUR,
            is_admin=False,
            member_count=members - sum(counts.values()),
            permissions=sorted(granted.get(EVERYONE_ELSE, ())),
        )
    )

    return ProjectPermissions(
        catalogue=_catalogue(),
        roles=lines,
        mine=sorted(await permissions.effective(session, project, principal)),
        may_manage=await roles.may_administer(session, project, principal),
    )


@router.put(
    "/projects/{project_ref}/roles/{role_ref}/permissions",
    response_model=RolePermissions,
    summary="Say what a role may do",
    responses={
        403: {"description": "You are not an admin of this project."},
        422: {"description": "The admin role's permissions are not configurable."},
    },
)
async def set_role_permissions(
    body: PermissionsUpdate,
    project: Project = Depends(resolved_project),
    role: ProjectRole = Depends(resolved_role),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """Replace what this role allows.

    Refused for the admin role. It holds everything by being the admin role,
    and an endpoint that let somebody untick "Membership" on it would be an
    endpoint for locking a board.
    """
    if role.is_admin:
        raise UnprocessableRequestError(
            f"The {role.name} role's permissions cannot be changed — an admin "
            "manages this project, which is the whole of what the role means.",
            details={"role_id": str(role.id)},
        )

    wanted = frozenset(body.permissions)
    await permissions.grant(session, project, role.id, wanted)
    await activity.record(
        session,
        principal,
        "role.permissions_set",
        entity_type="role",
        entity_id=role.id,
        project_id=project.id,
        payload={"name": role.name, "permissions": sorted(wanted)},
    )

    counts = await roles.holder_counts(session, project)
    return RolePermissions(
        role_id=role.id,
        name=role.name,
        colour=role.colour,
        is_admin=False,
        member_count=counts.get(role.id, 0),
        permissions=sorted(wanted),
    )


@router.put(
    "/projects/{project_ref}/permissions/everyone-else",
    response_model=RolePermissions,
    summary="Say what somebody with no role may do",
    responses={403: {"description": "You are not an admin of this project."}},
)
async def set_baseline_permissions(
    body: PermissionsUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """Replace what everybody here without a role may do.

    That is most of a board most of the time — a role is something an admin
    says about you, and until they have, this line is the whole of what you
    are allowed. It covers people who are not on the project at all for the
    same reason: membership has never been a fence in Cylist, and nobody
    having said what you are is nobody having said what you are.

    A project starts with every box on this line ticked, which is what every
    project was before roles carried permissions at all.
    """
    wanted = frozenset(body.permissions)
    await permissions.grant(session, project, EVERYONE_ELSE, wanted)
    await activity.record(
        session,
        principal,
        "role.permissions_set",
        entity_type="project",
        entity_id=project.id,
        project_id=project.id,
        payload={"name": EVERYONE_ELSE_NAME, "permissions": sorted(wanted)},
    )

    counts = await roles.holder_counts(session, project)
    return RolePermissions(
        role_id=EVERYONE_ELSE,
        name=EVERYONE_ELSE_NAME,
        colour=EVERYONE_ELSE_COLOUR,
        is_admin=False,
        member_count=len(project.members) - sum(counts.values()),
        permissions=sorted(wanted),
    )
