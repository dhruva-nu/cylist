"""What recording a change announces, and to whom."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from sqlalchemy import select

from app.auth.principal import Principal
from app.db import Database
from app.models.activity import Activity, Channel
from app.services import activity
from app.services.activity import trail

PRINCIPAL = Principal(
    token_id=None, person_id=None, label="test", scopes=frozenset(), channel=Channel.API
)


@pytest.fixture
def isolated() -> object:
    """The trail with no subscribers but the ones a test adds, restored after."""
    kept = list(trail._subscribers)
    trail._subscribers.clear()
    yield None
    trail._subscribers[:] = kept


async def record_one(database: Database) -> Activity:
    async with database.session() as session:
        entry = await activity.record(session, PRINCIPAL, "token.issued", entity_type="token")
        await session.flush()
        return entry


class TestSubscribing:
    async def test_every_subscriber_is_told_in_the_order_it_registered(
        self, database: Database, isolated: object
    ) -> None:
        told: list[str] = []

        async def first(_session: object, recorded: activity.Recorded) -> None:
            told.append(f"first:{recorded.entry.verb}")

        async def second(_session: object, recorded: activity.Recorded) -> None:
            told.append(f"second:{recorded.principal.label}")

        activity.subscribe(first)
        activity.subscribe(second)
        await record_one(database)

        assert told == ["first:token.issued", "second:test"]

    async def test_registering_twice_is_registering_once(
        self, database: Database, isolated: object
    ) -> None:
        told: list[int] = []

        async def once(_session: object, _recorded: activity.Recorded) -> None:
            told.append(1)

        activity.subscribe(once)
        activity.subscribe(once)
        await record_one(database)

        assert told == [1]

    async def test_a_subscriber_sees_the_row_before_it_is_committed(
        self, database: Database, isolated: object
    ) -> None:
        """It runs in the recording transaction — which is how a hook delivery can name the row."""
        seen: list[bool] = []

        async def look(session: object, recorded: activity.Recorded) -> None:
            seen.append(recorded.entry in session.new)  # type: ignore[attr-defined]

        activity.subscribe(look)
        await record_one(database)

        assert seen == [True]

    async def test_the_row_is_written_whether_or_not_anyone_listens(
        self, database: Database, isolated: object
    ) -> None:
        entry = await record_one(database)

        async with database.session() as session:
            found = await session.scalar(select(Activity.id).where(Activity.id == entry.id))
        assert found == entry.id

    async def test_the_app_registers_the_three_it_needs(self) -> None:
        """Built by `create_app`; a missing one would stop changes reaching hooks or boards."""
        names = {
            subscriber.__module__ + "." + subscriber.__name__ for subscriber in trail._subscribers
        }

        assert names >= {
            "app.services.agent_sessions.on_recorded",
            "app.realtime.doorbell.ring",
            "app.services.hooks.matching.on_recorded",
        }


class TestTheTrailImportsNoneOfIt:
    def test_the_trail_does_not_reach_into_hooks_sessions_or_the_realtime_layer(self) -> None:
        """The point of subscribing: it is what lets each of them import the trail."""
        tree = ast.parse(Path(trail.__file__).read_text())
        modules = {node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        services = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == "app.services"
            for alias in node.names
        }

        assert not [module for module in modules if "realtime" in module]
        assert services.isdisjoint({"hooks", "agent_sessions"})
