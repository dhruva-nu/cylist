"""Who may do what on a project, and the one place that refuses.

Three questions, and they are asked at different moments:

* :func:`effective` — what may this caller do here? Read by the screen, and by
  the client deciding which buttons are worth drawing.
* :func:`enforce` — may this caller do *this*? Called from a dependency on
  every write endpoint a role can fence, and the only thing that raises.
* :func:`for_project` — what does each role here allow? The grid itself.

Two callers hold everything, and neither is stored as a grant. An **admin**
holds every permission by being one, which is why deleting the admin role's
rows is not a way to lock a board: there are none. And the **bootstrap
session** — somebody signing in with ``CYLIST_PASSWORD_HASH`` before any
account exists — is not a person at all, so there is no role to read. Both
arrive here through :func:`app.services.roles.may_administer`, which already
holds the reasoning for the third case it admits: an ``admin``-scoped
credential on a project whose last admin was archived.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.permissions import ALL_PERMISSIONS, INFO, Permission, parse_permissions
from app.auth.principal import Principal
from app.core import sensitivity
from app.core.errors import ForbiddenError, NotFoundError
from app.core.sensitivity import DEFAULT_CLEARANCE, DEFAULT_LEVEL, Sensitivity
from app.models.board import BoardColumn
from app.models.permission import (
    EVERYONE_ELSE,
    ProjectPermission,
    RoleClearance,
    RoleColumnRule,
)
from app.models.project import Project, ProjectMember
from app.services import roles


async def seed(session: AsyncSession, project: Project) -> None:
    """Open a new project up, the way every project was open before roles.

    A board arrives permitting everybody on it everything, and an admin
    narrows it from there. The alternative — a new project that permits
    nothing until somebody ticks boxes — would make creating a board a
    two-step job and would have been a different answer to decision 4 than the
    one Cylist has given since the start: a project is a shared workspace, and
    a role is how an admin carves something narrower out of it when they want
    one.
    """
    await grant(session, project, EVERYONE_ELSE, ALL_PERMISSIONS)


async def grant(
    session: AsyncSession,
    project: Project,
    role_id: UUID | None,
    permitted: frozenset[Permission] | set[Permission],
) -> None:
    """Make ``permitted`` exactly what this role — or the baseline — allows.

    A replace rather than an add: the screen sends the whole row of the grid
    every time, and a diff would only be a way for two admins on two tabs to
    end up with a union of what each of them meant.
    """
    await session.execute(
        delete(ProjectPermission).where(
            ProjectPermission.project_id == project.id,
            ProjectPermission.role_id.is_(None)
            if role_id is None
            else ProjectPermission.role_id == role_id,
        )
    )
    # Flushed before the inserts: the delete and the insert both touch the
    # unique index, and re-granting a permission that was already there would
    # otherwise race its own removal within the one flush.
    await session.flush()

    session.add_all(
        ProjectPermission(project_id=project.id, role_id=role_id, permission=permission.value)
        for permission in sorted(permitted)
    )
    await session.flush()


async def for_project(
    session: AsyncSession, project: Project
) -> dict[UUID | None, frozenset[Permission]]:
    """Every grant on the project, keyed by role id and by :data:`EVERYONE_ELSE`.

    Roles with nothing ticked are absent rather than empty — the caller knows
    which roles exist and this only knows which ones were granted something —
    so read it with ``.get(role_id, frozenset())``.
    """
    rows = await session.scalars(
        select(ProjectPermission).where(ProjectPermission.project_id == project.id)
    )
    collected: dict[UUID | None, list[str]] = {}
    for row in rows:
        collected.setdefault(row.role_id, []).append(row.permission)
    return {role_id: parse_permissions(values) for role_id, values in collected.items()}


async def for_role(
    session: AsyncSession, project: Project, role_id: UUID | None
) -> frozenset[Permission]:
    """What one role — or the baseline — allows."""
    return (await for_project(session, project)).get(role_id, frozenset())


async def effective(
    session: AsyncSession, project: Project, principal: Principal
) -> frozenset[Permission]:
    """Everything this caller may do on this project.

    Their role's grants, or the project's baseline if they have no role — and
    the baseline is also the answer for somebody who is not on the project at
    all. Membership has never been a fence in Cylist (decision 4) and this
    ticket did not make it one: the question a role answers is what somebody
    *is* here, and somebody who is not here is the plainest case of nobody
    having said.
    """
    if await roles.may_administer(session, project, principal):
        return ALL_PERMISSIONS

    role_id = await _role_of(session, project, principal)
    if role_id is None:
        return await for_role(session, project, EVERYONE_ELSE)
    return await for_role(session, project, role_id)


async def enforce(
    session: AsyncSession,
    project: Project,
    principal: Principal,
    permission: Permission,
) -> Principal:
    """Admit a caller whose role allows ``permission``, or refuse and say so.

    The refusal names the board and finishes the sentence with the thing that
    was refused, because "forbidden" on a shared workspace is a question — is
    my token wrong, am I on the wrong project, has somebody changed what I am
    here — and the answer is cheap to give.
    """
    allowed = await effective(session, project, principal)
    if permission in allowed:
        return principal

    raise ForbiddenError(
        f"Your role on {project.key} does not let you {INFO[permission].refusal}.",
        details={"project_key": project.key, "permission": permission.value},
    )


async def enforce_on(
    session: AsyncSession,
    project_id: UUID,
    principal: Principal,
    permission: Permission,
) -> Principal:
    """:func:`enforce`, for the endpoints addressed by something on a project.

    A card is ``/tasks/ATL-41`` and a folder is ``/folders/{id}``: most of what
    a role fences is not reached through a path with the project in it, so the
    project is loaded from the row that was resolved on the way in.
    """
    project = await session.get(Project, project_id)
    if project is None:  # pragma: no cover - a resolved row's project exists
        raise NotFoundError("That project no longer exists.")
    return await enforce(session, project, principal, permission)


async def _role_of(session: AsyncSession, project: Project, principal: Principal) -> UUID | None:
    """Which of the project's roles this caller wears, if any.

    ``None`` covers three different people — not on the project, on it with no
    role, or a credential belonging to nobody — and they get the same answer
    on purpose: the baseline is what "nobody has said" means.
    """
    if principal.person_id is None:
        return None
    return await session.scalar(
        select(ProjectMember.role_id).where(
            ProjectMember.project_id == project.id,
            ProjectMember.person_id == principal.person_id,
        )
    )


# --- The per-object layer --------------------------------------------------
#
# Everything below is a *restriction*, where everything above is a grant. The
# flat permission decides whether a role may touch cards or read uploads at
# all; these only narrow what it already allowed, and the absence of a row is
# the absence of a narrowing. See `app.models.permission` for why the two
# layers have opposite polarity and why it has to be that way round.


async def column_rules(
    session: AsyncSession, project: Project, role_id: UUID | None
) -> dict[UUID, tuple[bool, bool]]:
    """What this role may do at each column it is restricted at.

    Keyed by column id, valued ``(may_enter, may_stage)``. Columns absent from
    the mapping carry no restriction, which is most of them on most boards —
    read it with ``.get(column_id, (True, True))``.
    """
    rows = await session.scalars(
        select(RoleColumnRule).where(
            RoleColumnRule.project_id == project.id,
            RoleColumnRule.role_id.is_(None)
            if role_id is None
            else RoleColumnRule.role_id == role_id,
        )
    )
    return {row.column_id: (row.may_enter, row.may_stage) for row in rows}


async def set_column_rules(
    session: AsyncSession,
    project: Project,
    role_id: UUID | None,
    wanted: dict[UUID, tuple[bool, bool]],
) -> None:
    """Make ``wanted`` exactly what this role may do across the board.

    Takes the whole board every time, for the reason a permission row does:
    the screen has the whole line in front of it. Rows that restrict nothing
    are deleted rather than stored, so the table stays the list of exceptions
    somebody deliberately made — which is the list worth reading when a board
    has stopped behaving and nobody remembers why.
    """
    await session.execute(
        delete(RoleColumnRule).where(
            RoleColumnRule.project_id == project.id,
            RoleColumnRule.role_id.is_(None)
            if role_id is None
            else RoleColumnRule.role_id == role_id,
        )
    )
    await session.flush()

    session.add_all(
        RoleColumnRule(
            project_id=project.id,
            role_id=role_id,
            column_id=column_id,
            may_enter=may_enter,
            may_stage=may_stage,
        )
        for column_id, (may_enter, may_stage) in sorted(wanted.items(), key=lambda row: str(row[0]))
        if not (may_enter and may_stage)
    )
    await session.flush()


async def at_column(
    session: AsyncSession, project: Project, principal: Principal, column_id: UUID
) -> tuple[bool, bool]:
    """What this caller may do at one column: ``(may_enter, may_stage)``.

    An admin — and anybody else :func:`app.services.roles.may_administer`
    admits — is unrestricted, the way they hold every flat permission.
    """
    if await roles.may_administer(session, project, principal):
        return (True, True)

    rules = await column_rules(session, project, await _role_of(session, project, principal))
    return rules.get(column_id, (True, True))


async def enforce_column_entry(
    session: AsyncSession, project: Project, principal: Principal, column_id: UUID
) -> None:
    """Refuse a move into a column this caller's role is kept out of.

    The refusal names the column rather than the permission, because unlike a
    flat refusal this one is about a specific place on a specific board, and
    "you cannot move cards into Done" is the whole of what somebody needs to
    hear to go and ask for it.
    """
    may_enter, _ = await at_column(session, project, principal, column_id)
    if may_enter:
        return

    column = await session.get(BoardColumn, column_id)
    where = column.name if column is not None else "that column"
    raise ForbiddenError(
        f"Your role on {project.key} does not let you move cards into {where}.",
        details={"project_key": project.key, "column_id": str(column_id)},
    )


async def enforce_column_staging(
    session: AsyncSession, project: Project, principal: Principal, column_id: UUID
) -> None:
    """Refuse setting the stages a card passes through in this column."""
    _, may_stage = await at_column(session, project, principal, column_id)
    if may_stage:
        return

    column = await session.get(BoardColumn, column_id)
    where = column.name if column is not None else "that column"
    raise ForbiddenError(
        f"Your role on {project.key} does not let you set the sub-stages for {where}.",
        details={"project_key": project.key, "column_id": str(column_id)},
    )


# --- How far a role may read -----------------------------------------------


async def clearances(session: AsyncSession, project: Project) -> dict[UUID | None, Sensitivity]:
    """Every clearance stored on this project, keyed the way the grid is.

    Roles absent from the mapping are cleared for everything — see
    :data:`~app.core.sensitivity.DEFAULT_CLEARANCE`.
    """
    rows = await session.scalars(
        select(RoleClearance).where(RoleClearance.project_id == project.id)
    )
    return {row.role_id: sensitivity.parse(row.level, DEFAULT_CLEARANCE) for row in rows}


async def set_clearance(
    session: AsyncSession, project: Project, role_id: UUID | None, level: Sensitivity
) -> None:
    """Say how sensitive a thing this role may read.

    The top clearance is stored as no row at all, for the reason a column rule
    that restricts nothing is: this table is the list of restrictions, and a
    row saying "may read everything" is not one.
    """
    await session.execute(
        delete(RoleClearance).where(
            RoleClearance.project_id == project.id,
            RoleClearance.role_id.is_(None)
            if role_id is None
            else RoleClearance.role_id == role_id,
        )
    )
    await session.flush()

    if level is not DEFAULT_CLEARANCE:
        session.add(RoleClearance(project_id=project.id, role_id=role_id, level=level.value))
        await session.flush()


async def clearance_of(
    session: AsyncSession, project: Project, principal: Principal
) -> Sensitivity:
    """How sensitive a thing this caller may read here."""
    if await roles.may_administer(session, project, principal):
        return DEFAULT_CLEARANCE

    stored = await clearances(session, project)
    return stored.get(await _role_of(session, project, principal), DEFAULT_CLEARANCE)


async def readable_levels(
    session: AsyncSession, project: Project, principal: Principal
) -> frozenset[Sensitivity]:
    """The levels this caller may read, for a ``WHERE … IN`` on a listing.

    A set rather than a ceiling because that is the shape a query wants, and
    because a filter written as ``IN`` cannot accidentally be written as the
    wrong side of a ``<``.
    """
    return sensitivity.at_most(await clearance_of(session, project, principal))


def readable(level: str, allowed: frozenset[Sensitivity]) -> bool:
    """Whether one stored level is within a caller's reach.

    Takes the raw string off the row: an unrecognised one is read as the
    default level rather than raising, so a row written by a newer Cylist is
    filtered on something sensible instead of breaking the listing it is in.
    """
    return sensitivity.parse(level, DEFAULT_LEVEL) in allowed


async def visible_levels_for(
    session: AsyncSession, project_id: UUID, principal: Principal
) -> frozenset[Sensitivity]:
    """:func:`readable_levels`, for a read reached through a row rather than a
    path that names the project."""
    project = await session.get(Project, project_id)
    if project is None:  # pragma: no cover - a resolved row's project exists
        return sensitivity.at_most(DEFAULT_CLEARANCE)
    return await readable_levels(session, project, principal)


def enforce_visible(level: str, allowed: frozenset[Sensitivity], what: str) -> None:
    """Refuse a read of something classified above the caller's clearance.

    **A 404, not a 403**, and that is the point of levels rather than of a
    permission: "you may not open this" has already told you it exists, which
    for a file called ``redundancy-list-final.xlsx`` is most of what there was
    to learn. Everything about the refusal — the status, the wording, the
    absence of any detail naming the row — is chosen so that a caller cannot
    tell a restricted thing from a thing that was never there.
    """
    if readable(level, allowed):
        return
    raise NotFoundError(f"No {what} with that id.")
