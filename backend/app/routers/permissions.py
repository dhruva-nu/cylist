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

from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import CATALOGUE, Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import UnprocessableRequestError
from app.core.sensitivity import CATALOGUE as SENSITIVITY_CATALOGUE
from app.core.sensitivity import DEFAULT_CLEARANCE, Sensitivity
from app.db import SessionDependency
from app.models.board import BoardColumn
from app.models.permission import EVERYONE_ELSE
from app.models.project import Project
from app.models.role import ProjectRole
from app.routers.projects import resolved_project
from app.routers.roles import project_admin, resolved_role
from app.schemas.permissions import (
    ClearanceUpdate,
    ColumnRule,
    ColumnRulesUpdate,
    PermissionInfoRead,
    PermissionsUpdate,
    ProjectPermissions,
    RolePermissions,
    SensitivityInfoRead,
)
from app.services import activity, columns, permissions, roles

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


def _permission_catalogue() -> list[PermissionInfoRead]:
    return [
        PermissionInfoRead(key=entry.key, label=entry.label, summary=entry.summary)
        for entry in CATALOGUE
    ]


def _sensitivity_levels() -> list[SensitivityInfoRead]:
    return [
        SensitivityInfoRead(key=entry.key, label=entry.label, summary=entry.summary)
        for entry in SENSITIVITY_CATALOGUE
    ]


async def _grid_line(
    session: AsyncSession,
    project: Project,
    *,
    role_id: UUID | None,
    name: str,
    colour: str,
    is_admin: bool,
    member_count: int,
) -> RolePermissions:
    """One line of the grid, read back from what is stored.

    Every endpoint on this router returns one of these, and all of them build
    it here: a PUT that echoed a hand-assembled line would be the place where
    the grid and the server quietly stopped agreeing.
    """
    granted = await permissions.for_project(session, project)
    cleared = await permissions.clearances(session, project)
    rules = await permissions.column_rules(session, project, role_id)
    board = await columns.list_for_project(session, project)

    return RolePermissions(
        role_id=role_id,
        name=name,
        colour=colour,
        is_admin=is_admin,
        member_count=member_count,
        # An admin's line is what it is rather than what is stored, and
        # nothing is stored: see `app.models.permission`.
        permissions=sorted(Permission) if is_admin else sorted(granted.get(role_id, ())),
        columns=_column_line(board, rules, unrestricted=is_admin),
        clearance=DEFAULT_CLEARANCE if is_admin else cleared.get(role_id, DEFAULT_CLEARANCE),
    )


async def _role_line(session: AsyncSession, project: Project, role: ProjectRole) -> RolePermissions:
    counts = await roles.holder_counts(session, project)
    return await _grid_line(
        session,
        project,
        role_id=role.id,
        name=role.name,
        colour=role.colour,
        is_admin=role.is_admin,
        member_count=counts.get(role.id, 0),
    )


async def _baseline_line(session: AsyncSession, project: Project) -> RolePermissions:
    counts = await roles.holder_counts(session, project)
    return await _grid_line(
        session,
        project,
        role_id=EVERYONE_ELSE,
        name=EVERYONE_ELSE_NAME,
        colour=EVERYONE_ELSE_COLOUR,
        is_admin=False,
        member_count=len(project.members) - sum(counts.values()),
    )


def _column_line(
    board: list[BoardColumn], rules: dict[UUID, tuple[bool, bool]], unrestricted: bool
) -> list[ColumnRule]:
    """A role's workflow line, one entry per column, in board order.

    Every column is reported rather than only the restricted ones. The stored
    table is a list of exceptions — see :class:`~app.models.permission.RoleColumnRule`
    — but a grid is not, and making the client work out which of the board's
    columns were missing and why is how a tick box ends up drawn wrong.
    """
    line: list[ColumnRule] = []
    for column in board:
        if unrestricted:
            may_enter, may_stage = True, True
        else:
            may_enter, may_stage = rules.get(column.id, (True, True))
        line.append(
            ColumnRule(
                column_id=column.id, name=column.name, may_enter=may_enter, may_stage=may_stage
            )
        )
    return line


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
    members = len(project.members)

    lines = [
        await _grid_line(
            session,
            project,
            role_id=role.id,
            name=role.name,
            colour=role.colour,
            is_admin=role.is_admin,
            member_count=counts.get(role.id, 0),
        )
        for role in found
    ]
    lines.append(
        await _grid_line(
            session,
            project,
            role_id=EVERYONE_ELSE,
            name=EVERYONE_ELSE_NAME,
            colour=EVERYONE_ELSE_COLOUR,
            is_admin=False,
            member_count=members - sum(counts.values()),
        )
    )

    return ProjectPermissions(
        catalogue=_permission_catalogue(),
        levels=_sensitivity_levels(),
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

    return await _role_line(session, project, role)


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

    return await _baseline_line(session, project)


@router.put(
    "/projects/{project_ref}/roles/{role_ref}/columns",
    response_model=RolePermissions,
    summary="Say where on the board a role may work",
    responses={
        403: {"description": "You are not an admin of this project."},
        422: {
            "description": (
                "The admin role's line is not configurable, or a column is not "
                "on this project's board."
            )
        },
    },
)
async def set_role_columns(
    body: ColumnRulesUpdate,
    project: Project = Depends(resolved_project),
    role: ProjectRole = Depends(resolved_role),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """Replace this role's line across the board.

    Two things per column, and they are different jobs: moving a card into
    Review is work, while deciding that a Hotfix in Review passes through
    "Drafted, Reviewed, Merged" is designing the workflow.

    Narrower than the `tasks` permission rather than instead of it — a role
    that may not touch cards at all is not asked about columns. And a column
    left out of the list is left unrestricted, which is also what a column
    added to the board tomorrow will be.
    """
    if role.is_admin:
        raise UnprocessableRequestError(
            f"The {role.name} role works anywhere on the board — an admin manages "
            "this project, which is the whole of what the role means.",
            details={"role_id": str(role.id)},
        )

    wanted = await _checked_columns(session, project, body)
    await permissions.set_column_rules(session, project, role.id, wanted)
    await _record_columns(session, principal, project, role.name, role.id, wanted)
    return await _role_line(session, project, role)


@router.put(
    "/projects/{project_ref}/permissions/everyone-else/columns",
    response_model=RolePermissions,
    summary="Say where on the board somebody with no role may work",
    responses={403: {"description": "You are not an admin of this project."}},
)
async def set_baseline_columns(
    body: ColumnRulesUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """The same, for everybody here that nobody has given a role to."""
    wanted = await _checked_columns(session, project, body)
    await permissions.set_column_rules(session, project, EVERYONE_ELSE, wanted)
    await _record_columns(session, principal, project, EVERYONE_ELSE_NAME, None, wanted)
    return await _baseline_line(session, project)


@router.put(
    "/projects/{project_ref}/roles/{role_ref}/clearance",
    response_model=RolePermissions,
    summary="Say how sensitive a thing a role may read",
    responses={
        403: {"description": "You are not an admin of this project."},
        422: {"description": "The admin role reads everything."},
    },
)
async def set_role_clearance(
    body: ClearanceUpdate,
    project: Project = Depends(resolved_project),
    role: ProjectRole = Depends(resolved_role),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """Set the most sensitive level this role may read.

    Uploads above it are not listed to its holders, not fetchable by id, and
    not downloadable — they are answered as though they were never there. See
    :mod:`app.core.sensitivity` for what the three levels mean.
    """
    if role.is_admin:
        raise UnprocessableRequestError(
            f"The {role.name} role reads everything on this project.",
            details={"role_id": str(role.id)},
        )

    await permissions.set_clearance(session, project, role.id, body.clearance)
    await _record_clearance(session, principal, project, role.name, role.id, body.clearance)
    return await _role_line(session, project, role)


@router.put(
    "/projects/{project_ref}/permissions/everyone-else/clearance",
    response_model=RolePermissions,
    summary="Say how sensitive a thing somebody with no role may read",
    responses={403: {"description": "You are not an admin of this project."}},
)
async def set_baseline_clearance(
    body: ClearanceUpdate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(project_admin),
    session: AsyncSession = SessionDependency,
) -> RolePermissions:
    """The same, for everybody here that nobody has given a role to.

    This is the one that decides whether an upload marked `restricted` is
    visible to the board at large, so it is the line most projects will set
    first.
    """
    await permissions.set_clearance(session, project, EVERYONE_ELSE, body.clearance)
    await _record_clearance(session, principal, project, EVERYONE_ELSE_NAME, None, body.clearance)
    return await _baseline_line(session, project)


async def _checked_columns(
    session: AsyncSession, project: Project, body: ColumnRulesUpdate
) -> dict[UUID, tuple[bool, bool]]:
    """Turn the sent line into rules, refusing a column from another board.

    Checked here rather than left to the foreign key: the key would refuse a
    column that does not exist, and say nothing about one that exists on
    somebody else's project — which is the mistake a client actually makes.
    """
    board = {column.id for column in await columns.list_for_project(session, project)}
    stray = [entry.column_id for entry in body.columns if entry.column_id not in board]
    if stray:
        raise UnprocessableRequestError(
            f"{len(stray)} of those columns are not on {project.key}'s board."
            if len(stray) > 1
            else f"That column is not on {project.key}'s board.",
            details={"column_ids": [str(column_id) for column_id in stray]},
        )
    return {entry.column_id: (entry.may_enter, entry.may_stage) for entry in body.columns}


async def _record_columns(
    session: AsyncSession,
    principal: Principal,
    project: Project,
    name: str,
    role_id: UUID | None,
    wanted: dict[UUID, tuple[bool, bool]],
) -> None:
    """Note what was narrowed, not what was left alone.

    The feed reads better for it, and the restrictions are the part somebody
    comes back to the log to understand.
    """
    restricted = sorted(
        str(column_id)
        for column_id, (may_enter, may_stage) in wanted.items()
        if not (may_enter and may_stage)
    )
    await activity.record(
        session,
        principal,
        "role.columns_set",
        entity_type="role" if role_id else "project",
        entity_id=role_id or project.id,
        project_id=project.id,
        payload={"name": name, "restricted_column_ids": restricted},
    )


async def _record_clearance(
    session: AsyncSession,
    principal: Principal,
    project: Project,
    name: str,
    role_id: UUID | None,
    clearance: Sensitivity,
) -> None:
    await activity.record(
        session,
        principal,
        "role.clearance_set",
        entity_type="role" if role_id else "project",
        entity_id=role_id or project.id,
        project_id=project.id,
        payload={"name": name, "clearance": clearance.value},
    )
