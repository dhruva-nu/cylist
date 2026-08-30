"""The people directory: create, amend, archive."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy import update as sql_update
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
        is_me=data.is_me,
    )
    if data.is_me:
        await _clear_me(session)
    session.add(person)
    await session.flush()
    return person


async def get_me(session: AsyncSession) -> Person | None:
    """The person marked as you, if anyone is."""
    person = await session.scalar(select(Person).where(Person.is_me.is_(True)))
    return person


async def _clear_me(session: AsyncSession, *, keep: UUID | None = None) -> None:
    """Take the flag off everyone else.

    The partial unique index would otherwise reject the second person to claim
    it, and refusing the claim is the wrong answer: saying "this is me" means
    the last one was wrong, not that this one is.
    """
    statement = sql_update(Person).where(Person.is_me.is_(True)).values(is_me=False)
    if keep is not None:
        statement = statement.where(Person.id != keep)
    await session.execute(statement)


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

    fields = data.model_dump(exclude_unset=True, exclude={"archived", "is_me"})
    for field, value in fields.items():
        setattr(person, field, value)

    if data.archived is not None:
        person.archived_at = now() if data.archived else None

    if data.is_me is not None:
        if data.is_me:
            await _clear_me(session, keep=person.id)
        person.is_me = data.is_me

    await session.flush()
    return person


async def archive(session: AsyncSession, person_id: UUID) -> Person:
    """Hide someone from the pickers, keeping every reference to them intact."""
    person = await get(session, person_id)
    if not person.is_archived:
        person.archived_at = now()
    # Archiving is how someone stops being on the projects; keeping "this is
    # you" on a row nobody can be assigned would only mean new projects were
    # created with a member they are not allowed to have.
    person.is_me = False
    return person
