"""The connection pool's limits, and handing a connection back mid-request (CYLIST-131)."""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeoutError

from app.config import Settings
from app.core.palette import colour_for
from app.db import Database, publish_into
from app.models.person import Person, PersonKind
from tests.connections import assert_no_connection_held, connections_held


def _person(name: str) -> Person:
    return Person(
        name=name,
        kind=PersonKind.TEAM,
        title="Engineer",
        responsibilities="Payments.",
        colour=colour_for(name),
    )


@asynccontextmanager
async def _pool(settings: Settings, **limits: Any) -> AsyncIterator[Database]:
    database = Database.configured(settings.model_copy(update=limits))
    try:
        yield database
    finally:
        await database.dispose()


class TestTheLimits:
    def test_they_are_settings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CYLIST_DATABASE_POOL_SIZE", "4")
        monkeypatch.setenv("CYLIST_DATABASE_MAX_OVERFLOW", "2")
        monkeypatch.setenv("CYLIST_DATABASE_POOL_TIMEOUT_SECONDS", "3.5")
        monkeypatch.setenv("CYLIST_DATABASE_STATEMENT_TIMEOUT_SECONDS", "12")
        read = Settings(_env_file=None)
        assert read.database_pool_size == 4
        assert read.database_max_overflow == 2
        assert read.database_pool_timeout_seconds == 3.5
        assert read.database_statement_timeout_seconds == 12

    async def test_a_full_pool_refuses_after_the_checkout_timeout(self, settings: Settings) -> None:
        async with (
            _pool(
                settings,
                database_pool_size=1,
                database_max_overflow=0,
                database_pool_timeout_seconds=0.2,
            ) as database,
            database.session() as holding,
        ):
            await holding.execute(select(1))
            started = time.monotonic()
            with pytest.raises(PoolTimeoutError):
                async with database.session() as waiting:
                    await waiting.execute(select(1))
            assert time.monotonic() - started < 5, "waited the default 30 s, not the setting"

    async def test_overflow_lends_connections_beyond_the_pool(self, settings: Settings) -> None:
        async with (
            _pool(
                settings,
                database_pool_size=1,
                database_max_overflow=1,
                database_pool_timeout_seconds=0.2,
            ) as database,
            database.session() as first,
            database.session() as second,
        ):
            await first.execute(select(1))
            await second.execute(select(1))
            assert connections_held(database) == 2

    async def test_a_statement_is_cancelled_at_the_statement_timeout(
        self, settings: Settings
    ) -> None:
        async with _pool(settings, database_statement_timeout_seconds=0.25) as database:
            async with database.session() as session:
                assert await session.scalar(text("SHOW statement_timeout")) == "250ms"
            with pytest.raises(DBAPIError, match="statement timeout"):
                async with database.session() as session:
                    await session.execute(text("SELECT pg_sleep(2)"))

    async def test_zero_turns_the_statement_timeout_off(self, settings: Settings) -> None:
        async with (
            _pool(settings, database_statement_timeout_seconds=0) as database,
            database.session() as session,
        ):
            assert await session.scalar(text("SHOW statement_timeout")) == "0"


class TestReleasing:
    async def test_it_commits_tells_and_hands_the_connection_back(self, database: Database) -> None:
        told: list[dict[str, Any]] = []
        database.publish_to(told.append)
        try:
            async with database.session() as session:
                session.add(_person("Aditi K"))
                publish_into(session, {"type": "board.changed"})
                await session.flush()
                assert connections_held(database) == 1

                await database.release(session)

                assert connections_held(database) == 0
                assert told == [{"type": "board.changed"}], "told once it committed"
                async with database.session() as other:
                    seen = await other.scalar(select(Person.name).where(Person.name == "Aditi K"))
                assert seen == "Aditi K", "committed, so another connection reads it"

                # And the session carries on, on a connection of its own.
                session.add(_person("Ravi S"))
                await session.flush()
            async with database.session() as other:
                names = set(await other.scalars(select(Person.name)))
            assert {"Aditi K", "Ravi S"} <= names
            assert told == [{"type": "board.changed"}], "nothing told twice"
        finally:
            database.publish_to(None)


class TestTheCheck:
    async def test_it_fails_while_a_session_holds_a_connection(self, database: Database) -> None:
        assert_no_connection_held(database, "nothing")
        async with database.session() as session:
            await session.execute(select(1))
            with pytest.raises(AssertionError, match="held while waiting on jev"):
                assert_no_connection_held(database, "jev")
        assert_no_connection_held(database, "nothing")
