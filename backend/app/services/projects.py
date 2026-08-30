"""Projects and their membership.

Project lookups accept either a UUID or a project key, so an agent can address
``/projects/ATL`` without first resolving an id.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.palette import colour_for
from app.models.person import Person, PersonKind
from app.models.project import Project, ProjectMember
from app.schemas.projects import ProjectCreate, ProjectUpdate
from app.services import columns, files


async def create(session: AsyncSession, data: ProjectCreate) -> Project:
    """Start a new project, board and file tree included.

    The starter columns and the root folder are part of creating a project
    rather than separate steps. A board with no columns cannot hold a task and
    a tree with no root cannot hold a file, so in both cases there would be
    nothing useful to do with the project until someone had run a setup step
    whose outcome was never in doubt.

    Raises:
        ConflictError: if the key is already taken.
    """
    project = Project(
        key=data.key,
        name=data.name,
        description=data.description,
        colour=data.colour or colour_for(data.key),
    )
    session.add(project)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"The key {data.key} is already used by another project.",
            details={"key": data.key},
        ) from exc

    await columns.seed(session, project)
    await files.seed_root(session, project)

    # `members` is only populated by a SELECT, and a just-inserted row has not
    # had one. Load it now so callers can read it without lazy IO.
    await session.refresh(project, ["members"])
    return project


async def resolve(session: AsyncSession, reference: str) -> Project:
    """Look a project up by id or by key.

    Args:
        reference: A UUID, or a project key such as ``ATL`` (case-insensitive).

    Raises:
        NotFoundError: if nothing matches.
    """
    try:
        statement = select(Project).where(Project.id == UUID(reference))
    except ValueError:
        statement = select(Project).where(Project.key == reference.upper())

    project = await session.scalar(statement)
    if project is None:
        raise NotFoundError(f"No project matching {reference!r}.")
    return project


async def list_projects(session: AsyncSession, *, include_archived: bool = False) -> list[Project]:
    """Return projects, newest first."""
    statement = select(Project).order_by(Project.created_at.desc())
    if not include_archived:
        statement = statement.where(Project.archived_at.is_(None))
    return list(await session.scalars(statement))


async def update(session: AsyncSession, project: Project, data: ProjectUpdate) -> Project:
    """Apply a partial update.

    A new name reaches the root folder too — it stands for the project, so a
    tree headed by the old name would be describing something that no longer
    exists.
    """
    fields = data.model_dump(exclude_unset=True, exclude={"archived"})
    for field, value in fields.items():
        setattr(project, field, value)

    if data.archived is not None:
        project.archived_at = now() if data.archived else None

    try:
        await session.flush()
    except IntegrityError as exc:
        raise ConflictError(
            f"The key {data.key} is already used by another project.",
            details={"key": data.key},
        ) from exc

    if fields.get("name") is not None:
        await files.rename_root(session, project)

    # `members` is only populated by a SELECT, and a just-inserted row has not
    # had one. Load it now so callers can read it without lazy IO.
    await session.refresh(project, ["members"])
    return project


async def archive(session: AsyncSession, project: Project) -> Project:
    """Hide a project. Its board, files and vault are left untouched."""
    if not project.is_archived:
        project.archived_at = now()
    return project


async def set_members(
    session: AsyncSession, project: Project, person_ids: list[UUID]
) -> list[Person]:
    """Replace the project's membership with exactly these people.

    Raises:
        UnprocessableRequestError: if any id is not in the directory, or names
            someone who has been archived.
    """
    people = await _load_people(session, person_ids)

    await session.execute(delete(ProjectMember).where(ProjectMember.project_id == project.id))
    session.add_all(
        ProjectMember(project_id=project.id, person_id=person_id) for person_id in person_ids
    )
    await session.flush()

    # The relationship was loaded before the rewrite; drop it so the next read
    # reflects what we just wrote.
    await session.refresh(project, ["members"])
    return people


async def _load_people(session: AsyncSession, person_ids: list[UUID]) -> list[Person]:
    if not person_ids:
        return []

    found = {
        person.id: person
        for person in await session.scalars(select(Person).where(Person.id.in_(person_ids)))
    }

    missing = [str(person_id) for person_id in person_ids if person_id not in found]
    if missing:
        raise UnprocessableRequestError(
            "Those people are not in the directory.", details={"unknown_person_ids": missing}
        )

    archived = [str(person.id) for person in found.values() if person.is_archived]
    if archived:
        raise UnprocessableRequestError(
            "Archived people cannot be added to a project.",
            details={"archived_person_ids": archived},
        )

    return [found[person_id] for person_id in person_ids]


async def require_members(
    session: AsyncSession, project_id: UUID, person_ids: list[UUID]
) -> list[Person]:
    """Check that every id names someone on this project, and return them.

    Assignees and "waiting on" tags both go through here. Membership is the
    line: naming someone who is not on the project produces a board that
    claims a person is involved when nobody has agreed that they are.

    Raises:
        UnprocessableRequestError: if any id is not a member.
    """
    if not person_ids:
        return []

    found = {
        person.id: person
        for person in await session.scalars(
            select(Person)
            .join(ProjectMember, ProjectMember.person_id == Person.id)
            .where(ProjectMember.project_id == project_id, Person.id.in_(person_ids))
        )
    }

    missing = [str(person_id) for person_id in person_ids if person_id not in found]
    if missing:
        raise UnprocessableRequestError(
            "Those people are not on this project. Add them under People first.",
            details={"non_member_person_ids": missing},
        )

    return [found[person_id] for person_id in person_ids]


async def member_counts(session: AsyncSession, project: Project) -> dict[PersonKind, int]:
    """Count members of each kind in one query."""
    rows = await session.execute(
        select(Person.kind, func.count())
        .join(ProjectMember, ProjectMember.person_id == Person.id)
        .where(ProjectMember.project_id == project.id)
        .group_by(Person.kind)
    )
    counts = dict.fromkeys(PersonKind, 0)
    for kind, count in rows:
        counts[kind] = count
    return counts
