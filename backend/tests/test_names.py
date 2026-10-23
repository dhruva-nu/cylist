"""The shared ways of refusing a name that is taken, and a person who is not there."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from app.core.errors import ConflictError, Reason, UnprocessableRequestError
from app.db import Database
from app.models.person import Person, PersonKind
from app.services import names, people


class TestEnsureFree:
    async def test_passes_when_nothing_holds_the_name(self, database: Database) -> None:
        async with database.session() as session:
            await names.ensure_free(
                session,
                select(Person.id).where(Person.name == "nobody by this name"),
                lambda _: names.taken("Taken."),
            )

    async def test_refuses_with_what_it_found(self, database: Database) -> None:
        async with database.session() as session:
            person = Person(
                name="Aditi K",
                kind=PersonKind.TEAM,
                title="Engineer",
                responsibilities="Payments.",
                colour="#4A7B8C",
            )
            session.add(person)
            await session.flush()

            with pytest.raises(ConflictError) as refused:
                await names.ensure_free(
                    session,
                    select(Person).where(Person.name == "Aditi K"),
                    lambda held: names.taken(f"{held.name} is taken.", person_id=str(held.id)),
                )

        assert refused.value.message == "Aditi K is taken."
        assert refused.value.reason is Reason.DUPLICATE_NAME
        assert refused.value.details == {"person_id": str(person.id)}


class TestEnsurePersonExists:
    async def test_nobody_is_always_fine(self, database: Database) -> None:
        async with database.session() as session:
            await people.ensure_person_exists(session, None)

    async def test_refuses_a_person_who_is_not_in_the_directory(self, database: Database) -> None:
        async with database.session() as session:
            with pytest.raises(UnprocessableRequestError):
                await people.ensure_person_exists(session, uuid4())
