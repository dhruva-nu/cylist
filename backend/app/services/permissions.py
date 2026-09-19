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
from app.core.errors import ForbiddenError, NotFoundError
from app.models.permission import EVERYONE_ELSE, ProjectPermission
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
