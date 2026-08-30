"""The people directory: create, amend, archive."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now
from app.core.errors import NotFoundError
from app.core.palette import colour_for
from app.models.person import KIND_ORDER, Person, PersonKind
from app.schemas.people import PersonCreate, PersonUpdate


async def create(session: AsyncSession, data: PersonCreate) -> Person:
    """Add someone to the directory."""
    person = Person(
        name=data.name,
        kind=data.kind,
        role=data.role,
        responsibilities=data.responsibilities,
        email=data.email,
        colour=data.colour or colour_for(data.name),
    )
    session.add(person)
    await session.flush()
    return person


async def get(session: AsyncSession, person_id: UUID) -> Person:
    """Fetch one person, archived or not."""
    person = await session.get(Person, person_id)
    if person is None:
        raise NotFoundError("No person with that id.")
    return person


async def list_people(
    session: AsyncSession,
    *,
    kind: PersonKind | None = None,
    include_archived: bool = False,
) -> list[Person]:
    """Return the directory, team first, alphabetical within each kind."""
    statement = select(Person).order_by(KIND_ORDER, Person.name)
    if kind is not None:
        statement = statement.where(Person.kind == kind)
    if not include_archived:
        statement = statement.where(Person.archived_at.is_(None))
    return list(await session.scalars(statement))


async def update(session: AsyncSession, person_id: UUID, data: PersonUpdate) -> Person:
    """Apply a partial update.

    ``archived`` is translated to a timestamp here rather than exposing
    ``archived_at`` for a client to set to an arbitrary moment.
    """
    person = await get(session, person_id)

    fields = data.model_dump(exclude_unset=True, exclude={"archived"})
    for field, value in fields.items():
        setattr(person, field, value)

    if data.archived is not None:
        person.archived_at = now() if data.archived else None

    return person


async def archive(session: AsyncSession, person_id: UUID) -> Person:
    """Hide someone from the pickers, keeping every reference to them intact."""
    person = await get(session, person_id)
    if not person.is_archived:
        person.archived_at = now()
    return person
