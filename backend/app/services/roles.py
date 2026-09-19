"""A project's roles, who holds them, and the rules that keep one admin.

Roles are addressed by id or by name, the way a project is addressed by id or
by key, so ``/projects/ATL/roles/Reviewer`` works and a later CLI surface has a
path in that does not start with a UUID.

Two rules live here rather than in the router, because both need to look at
rows to answer and both have to name what they found:

* **A role that somebody holds is not deleted.** The refusal lists the holders,
  the way a goal's refuses with the cards holding it open.
* **A project does not lose its last admin.** Neither by having the role taken
  off its last holder nor by having that holder dropped from the project.

Archived people are not counted as holders for the second rule. They cannot
sign in, so counting them would let a project look administered while nobody
could administer it — and :func:`app.services.roles.may_administer` has a
matching escape hatch for the case where that has already happened.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.palette import colour_for
from app.models.person import KIND_ORDER, Person
from app.models.project import Project, ProjectMember
from app.models.role import ADMIN_ROLE_NAME, ProjectRole
from app.schemas.roles import RoleCreate, RoleUpdate

_ORDER = (ProjectRole.is_admin.desc(), ProjectRole.name)
"""Admin first, then alphabetical. The one role with authority attached is the
one a reader is looking for, and it is never the one a name sort would put at
the top."""


async def seed_admin(session: AsyncSession, project: Project) -> ProjectRole:
    """Give a new project the one role Cylist makes for it.

    Part of creating a project, like its starter columns and its root folder:
    a project whose roles nobody can create is a project with no way to grow
    the first one.
    """
    role = ProjectRole(
        project_id=project.id,
        name=ADMIN_ROLE_NAME,
        description="Manages this project's roles and who holds them.",
        colour=colour_for(f"{project.key}:{ADMIN_ROLE_NAME}"),
        is_admin=True,
    )
    session.add(role)
    await session.flush()
    return role


async def list_roles(session: AsyncSession, project: Project) -> list[ProjectRole]:
    """Every role on the project, admin first."""
    return list(
        await session.scalars(
            select(ProjectRole).where(ProjectRole.project_id == project.id).order_by(*_ORDER)
        )
    )


async def holder_counts(session: AsyncSession, project: Project) -> dict[UUID, int]:
    """How many members hold each of the project's roles, in one query."""
    rows = await session.execute(
        select(ProjectMember.role_id, func.count())
        .where(ProjectMember.project_id == project.id, ProjectMember.role_id.is_not(None))
        .group_by(ProjectMember.role_id)
    )
    return {role_id: count for role_id, count in rows if role_id is not None}


async def member_roles(session: AsyncSession, project: Project) -> dict[UUID, ProjectRole]:
    """Each member's role, keyed by person id, for the people who have one.

    A query rather than a relationship on :class:`~app.models.project.Project`:
    ``Project.members`` goes through ``secondary=``, so it yields people and
    can never see the row in between them and the project — which is where the
    role is.
    """
    rows = await session.execute(
        select(ProjectMember.person_id, ProjectRole)
        .join(ProjectRole, ProjectRole.id == ProjectMember.role_id)
        .where(ProjectMember.project_id == project.id)
    )
    return dict(rows.tuples().all())


async def admin_role(session: AsyncSession, project: Project) -> ProjectRole | None:
    """The project's admin role, if it has one.

    ``None`` only for a project created before CYLIST-45 whose back-fill has
    somehow not run; every project made since has one.
    """
    found: ProjectRole | None = await session.scalar(
        select(ProjectRole).where(
            ProjectRole.project_id == project.id, ProjectRole.is_admin.is_(True)
        )
    )
    return found


async def admin_holder_ids(session: AsyncSession, project: Project) -> set[UUID]:
    """Who can administer this project today — archived people excluded."""
    return set(
        await session.scalars(
            select(ProjectMember.person_id)
            .join(ProjectRole, ProjectRole.id == ProjectMember.role_id)
            .join(Person, Person.id == ProjectMember.person_id)
            .where(
                ProjectMember.project_id == project.id,
                ProjectRole.is_admin.is_(True),
                Person.archived_at.is_(None),
            )
        )
    )


async def may_administer(session: AsyncSession, project: Project, principal: Principal) -> bool:
    """Whether this caller may create, change and hand out the project's roles.

    Holding the admin role is the answer the ticket asks for. Two other
    callers pass, and both are about not painting a project into a corner:

    * **The bootstrap session**, which has no person at all — it is the
      deployment owner signing in with ``CYLIST_PASSWORD_HASH`` before any
      account exists, and it is what opens the first one.
    * **Anyone holding** :attr:`~app.auth.scopes.Scope.ADMIN` **on a project
      with no admin left**. The rules below stop a project losing its last
      admin by any ordinary route, but archiving that person is not an
      ordinary route and does not consult this module. Without a way back, a
      project would be unadministrable forever, which is a worse answer than
      letting whoever manages the deployment's tokens step in.
    """
    if principal.person_id is None:
        return True

    holders = await admin_holder_ids(session, project)
    if principal.person_id in holders:
        return True

    return not holders and principal.has(Scope.ADMIN)


async def resolve(session: AsyncSession, project: Project, reference: str) -> ProjectRole:
    """Look a role up on this project by id or by name.

    Args:
        reference: A UUID, or a role name such as ``Reviewer`` (case-insensitive).

    Raises:
        NotFoundError: if nothing on this project matches.
    """
    statement = select(ProjectRole).where(ProjectRole.project_id == project.id)
    try:
        statement = statement.where(ProjectRole.id == UUID(reference))
    except ValueError:
        statement = statement.where(func.lower(ProjectRole.name) == reference.strip().lower())

    role = await session.scalar(statement)
    if role is None:
        raise NotFoundError(f"No role matching {reference!r} on {project.key}.")
    return role


async def create(session: AsyncSession, project: Project, data: RoleCreate) -> ProjectRole:
    """Add a role to the project.

    Raises:
        ConflictError: if the project already has a role by that name.
    """
    # Read before the flush: a failed one expires the project's attributes,
    # and reaching for `project.key` to write the message would then go back
    # to a session that is no longer willing to answer.
    key = project.key

    role = ProjectRole(
        project_id=project.id,
        name=data.name,
        description=data.description,
        colour=data.colour or colour_for(f"{key}:{data.name}"),
    )
    session.add(role)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"{key} already has a role called {data.name}.",
            details={"name": data.name},
        ) from exc
    return role


async def update(session: AsyncSession, role: ProjectRole, data: RoleUpdate) -> ProjectRole:
    """Apply a partial update.

    Raises:
        ConflictError: if the new name is already taken on this project.
        UnprocessableRequestError: if this is the admin role and the change
            would rename it.
    """
    fields = data.model_dump(exclude_unset=True)
    if role.is_system and "name" in fields and fields["name"] != role.name:
        raise UnprocessableRequestError(
            f"The {role.name} role cannot be renamed — every explanation of who "
            "may do what on a project names it.",
            details={"role_id": str(role.id)},
        )

    for field, value in fields.items():
        setattr(role, field, value)

    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"That project already has a role called {fields.get('name')}.",
            details={"name": fields.get("name")},
        ) from exc
    return role


async def delete(session: AsyncSession, project: Project, role: ProjectRole) -> None:
    """Remove a role from the project.

    Raises:
        UnprocessableRequestError: if this is the admin role, or if anybody
            still holds it — in which case the refusal names them.
    """
    if role.is_system:
        raise UnprocessableRequestError(
            f"The {role.name} role cannot be deleted — it is what makes anyone "
            "able to manage the others.",
            details={"role_id": str(role.id)},
        )

    wearing = await holders(session, project, role)
    if wearing:
        raise UnprocessableRequestError(
            f"{_and_list([person.name for person in wearing])} still "
            f"{'holds' if len(wearing) == 1 else 'hold'} the {role.name} role. "
            "Move them off it first.",
            details={"holder_person_ids": [str(person.id) for person in wearing]},
        )

    await session.delete(role)
    await session.flush()


async def holders(session: AsyncSession, project: Project, role: ProjectRole) -> list[Person]:
    """Everyone on the project wearing this role, team before clients."""
    return list(
        await session.scalars(
            select(Person)
            .join(ProjectMember, ProjectMember.person_id == Person.id)
            .where(
                ProjectMember.project_id == project.id,
                ProjectMember.role_id == role.id,
            )
            .order_by(KIND_ORDER, Person.name)
        )
    )


async def set_member_role(
    session: AsyncSession,
    project: Project,
    person: Person,
    role: ProjectRole | None,
) -> ProjectMember:
    """Put a role on a member, or take theirs off.

    Raises:
        UnprocessableRequestError: if they are not on the project, or if this
            would take the admin role off its last holder.
    """
    membership = await session.get(ProjectMember, (project.id, person.id))
    if membership is None:
        raise UnprocessableRequestError(
            f"{person.name} is not on {project.key}. Add them under People first.",
            details={"non_member_person_ids": [str(person.id)]},
        )

    if membership.role_id != (role.id if role else None):
        await ensure_an_admin_remains(session, project, losing={person.id})

    membership.role_id = role.id if role else None
    await session.flush()
    return membership


async def ensure_an_admin_remains(
    session: AsyncSession, project: Project, *, losing: set[UUID]
) -> None:
    """Refuse a role change that would leave the project with no admin.

    Only the deliberate route — somebody saying "this person is now a
    Reviewer" about the last admin there is. The intent is unambiguous and so
    is the fix, which is why this refuses and names them rather than quietly
    arranging something: give the role to somebody else first.

    Dropping the last admin from the project is *not* this. That caller is
    saying who is on the board, not who administers it, and the answer there
    is :func:`fill_admin_vacancy`.

    Args:
        losing: The people who would stop being admins.
    """
    holder_ids = await admin_holder_ids(session, project)
    if not holder_ids or holder_ids - losing:
        return

    remaining = await session.scalars(select(Person).where(Person.id.in_(holder_ids)))
    names = _and_list(sorted(person.name for person in remaining))
    raise UnprocessableRequestError(
        f"{names} would be the last {ADMIN_ROLE_NAME.lower()} on {project.key}, "
        "and a project cannot be left with nobody able to manage it. Give the "
        f"{ADMIN_ROLE_NAME} role to somebody else first.",
        details={"admin_person_ids": [str(person_id) for person_id in sorted(holder_ids)]},
    )


async def fill_admin_vacancy(session: AsyncSession, project: Project) -> Person | None:
    """Give the admin role to the longest-standing member, if nobody holds it.

    Two situations reach here and they are the same situation. A project
    created by the bootstrap session starts with no members, so there was
    nobody to be its admin; and a project whose last admin is removed from it
    has just stopped having one. Either way the role is on the project and
    unworn, and leaving it that way would mean a board nobody could add a role
    to.

    Longest-standing member first — by when they joined, tie-broken by when
    they were added to the directory — which is the proxy revision 0024 used
    to pick an admin for every project that predated roles, and the nearest
    thing a board has to "who has been here since the start". Somebody who
    already wears a role of their own is skipped rather than overwritten: a
    vacancy is not a reason to quietly change what somebody is.

    Returns:
        Whoever became the admin, or ``None`` if nobody needed to or nobody
        could — an empty project, or one whose every member already has a role.
    """
    if await admin_holder_ids(session, project):
        return None

    role = await admin_role(session, project)
    if role is None:
        return None

    candidate = await session.execute(
        select(ProjectMember, Person)
        .join(Person, Person.id == ProjectMember.person_id)
        .where(
            ProjectMember.project_id == project.id,
            ProjectMember.role_id.is_(None),
            Person.archived_at.is_(None),
        )
        .order_by(ProjectMember.created_at, Person.created_at, Person.name)
        .limit(1)
    )
    row = candidate.first()
    if row is None:
        return None

    membership: ProjectMember = row[0]
    person: Person = row[1]
    membership.role_id = role.id
    await session.flush()
    return person


def _and_list(names: list[str]) -> str:
    """Join names the way a sentence does: a, b and c."""
    if len(names) <= 1:
        return names[0] if names else ""
    return f"{', '.join(names[:-1])} and {names[-1]}"
