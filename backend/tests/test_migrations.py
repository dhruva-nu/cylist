"""What the migrations do to data that is already in the database.

Every other test builds its schema straight from the ORM metadata, which proves
nothing about the SQL that gets an *existing* database to that shape. These run
the real revisions against a scratch database, with rows in it, so a back-fill
is tested on the thing it was written for rather than on an empty table.

The scratch database sits beside the test one on the same server, because
``alembic_version`` and the migrations themselves would otherwise fight with
the fixtures that truncate and recreate the test schema.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncConnection, create_async_engine

from alembic import command
from app.config import get_settings

BACKEND_ROOT = Path(__file__).resolve().parent.parent
SCRATCH_DATABASE = "cylist_migration_data"

BEFORE_ROOTS = "0005"
WITH_ROOTS = "0006"

PROJECTS = [
    ("ATL", "Atlas Billing Migration"),
    ("HRM", "Hermes Notifications"),
    ("ORB", "Orbit Internal Portal"),
]


async def _on_maintenance_db(url: URL, statements: list[str]) -> None:
    """Run statements against the server's default database.

    CREATE and DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


@pytest.fixture
async def scratch(database_url: str, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[URL]:
    """An empty database on the test server, dropped when the test ends."""
    base = make_url(database_url)
    await _on_maintenance_db(
        base,
        [
            f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}"',
            f'CREATE DATABASE "{SCRATCH_DATABASE}"',
        ],
    )

    url = base.set(database=SCRATCH_DATABASE)
    # env.py reads the URL from the settings, whose cache is process-wide, so
    # pointing Alembic anywhere means pointing that.
    monkeypatch.setenv("CYLIST_DATABASE_URL", url.render_as_string(hide_password=False))
    get_settings.cache_clear()
    try:
        yield url
    finally:
        get_settings.cache_clear()
        await _on_maintenance_db(base, [f'DROP DATABASE IF EXISTS "{SCRATCH_DATABASE}"'])


def _alembic_config() -> Config:
    """Alembic pointed at this repository.

    The URL is deliberately left out: ``env.py`` overwrites it from the
    settings anyway, and an embedded server's socket path arrives full of
    percent signs that ConfigParser reads as interpolation.
    """
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "alembic"))
    return config


def _run_alembic(step: Callable[[Config, str], None]) -> Callable[[str], Awaitable[None]]:
    """Wrap an Alembic command so a test can await it.

    In a thread because ``env.py`` calls :func:`asyncio.run`, which refuses to
    start a second loop inside the one pytest-asyncio already has running.
    """
    config = _alembic_config()

    async def run(revision: str) -> None:
        target = make_url(os.environ["CYLIST_DATABASE_URL"]).database
        assert target == SCRATCH_DATABASE, f"refusing to migrate {target}"
        await asyncio.to_thread(step, config, revision)

    return run


@pytest.fixture
def migrate(scratch: URL) -> Callable[[str], Awaitable[None]]:
    """Move the scratch database forward to a revision."""
    return _run_alembic(command.upgrade)


@pytest.fixture
def rewind(scratch: URL) -> Callable[[str], Awaitable[None]]:
    """Move the scratch database back to a revision."""
    return _run_alembic(command.downgrade)


@pytest.fixture
async def connection(scratch: URL) -> AsyncIterator[AsyncConnection]:
    """A connection to the scratch database that commits what it writes."""
    engine = create_async_engine(scratch, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as open_connection:
            yield open_connection
    finally:
        await engine.dispose()


async def seed_pre_0006(connection: AsyncConnection) -> dict[str, Any]:
    """Fill the database with the shape revision 0005 allowed.

    Three projects with different problems in them: one with a nested tree and
    files, one whose top-level folder is named exactly like the project — which
    is what the old unique index would have collided with — and one with no
    folders at all.
    """
    for key, name in PROJECTS:
        await connection.execute(
            text(
                "INSERT INTO project (id, key, name, description, colour, task_counter)"
                " VALUES (gen_random_uuid(), :key, :name, '', '#1D7D46', 0)"
            ),
            {"key": key, "name": name},
        )

    async def add_folder(project_key: str, name: str, parent: str | None = None) -> str:
        row = await connection.execute(
            text(
                "INSERT INTO folder (id, project_id, parent_id, name)"
                " SELECT gen_random_uuid(), project.id, :parent, :name"
                " FROM project WHERE project.key = :key"
                " RETURNING id"
            ),
            {"key": project_key, "name": name, "parent": parent},
        )
        return str(row.scalar_one())

    architecture = await add_folder("ATL", "Architecture")
    finance = await add_folder("ATL", "Finance inputs")
    year = await add_folder("ATL", "2025", finance)
    # A top-level folder wearing the project's own name. Before 0006 that was
    # legal; the root the back-fill inserts wants the same name.
    namesake = await add_folder("HRM", "Hermes Notifications")

    digest = "a" * 64
    await connection.execute(
        text(
            "INSERT INTO blob (id, sha256, size, mime, path)"
            " VALUES (gen_random_uuid(), :sha, 42, 'application/pdf', :path)"
        ),
        {"sha": digest, "path": f"ab/cd/{digest}"},
    )
    await connection.execute(
        text(
            "INSERT INTO file_item (id, folder_id, kind, name, blob_id, source, size, mime)"
            " SELECT gen_random_uuid(), :folder, 'file', 'brief.pdf', blob.id, 'upload', 42,"
            " 'application/pdf' FROM blob WHERE blob.sha256 = :sha"
        ),
        {"folder": architecture, "sha": digest},
    )
    await connection.execute(
        text(
            "INSERT INTO file_item (id, folder_id, kind, name, url, source)"
            " VALUES (gen_random_uuid(), :folder, 'link', 'Tax portal',"
            " 'https://tax.example/atlas', 'other')"
        ),
        {"folder": year},
    )

    return {
        "architecture": architecture,
        "finance": finance,
        "year": year,
        "namesake": namesake,
    }


async def roots(connection: AsyncConnection) -> dict[str, tuple[str, str]]:
    """Every project's parentless folder, keyed by project key."""
    rows = await connection.execute(
        text(
            "SELECT project.key, folder.id, folder.name FROM folder"
            " JOIN project ON project.id = folder.project_id"
            " WHERE folder.parent_id IS NULL"
        )
    )
    return {key: (str(folder_id), name) for key, folder_id, name in rows}


async def parent_of(connection: AsyncConnection, folder_id: str) -> str | None:
    parent = await connection.scalar(
        text("SELECT parent_id FROM folder WHERE id = :id"), {"id": folder_id}
    )
    return str(parent) if parent is not None else None


class TestBackfillingRootFolders:
    """Revision 0006 against a database that already holds folders and files."""

    async def test_gives_every_project_exactly_one_root(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        assert sorted(await roots(connection)) == ["ATL", "HRM", "ORB"]

    async def test_names_each_root_after_its_project(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        found = await roots(connection)
        assert {key: name for key, (_, name) in found.items()} == dict(PROJECTS)

    async def test_gives_a_root_to_a_project_with_no_folders(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        orbit, _ = (await roots(connection))["ORB"]
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM folder WHERE parent_id = :id"), {"id": orbit}
            )
            == 0
        )

    async def test_reparents_what_used_to_be_top_level(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        before = await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        atlas, _ = (await roots(connection))["ATL"]
        assert await parent_of(connection, before["architecture"]) == atlas
        assert await parent_of(connection, before["finance"]) == atlas

    async def test_leaves_deeper_folders_where_they_were(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        """Only the top level moves; the shape below it is already right."""
        await migrate(BEFORE_ROOTS)
        before = await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        assert await parent_of(connection, before["year"]) == before["finance"]

    async def test_survives_a_folder_named_after_its_own_project(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        """The old unique index would have refused the root; it comes off first."""
        await migrate(BEFORE_ROOTS)
        before = await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        hermes, name = (await roots(connection))["HRM"]
        assert name == "Hermes Notifications"
        assert await parent_of(connection, before["namesake"]) == hermes

    async def test_keeps_every_file_where_it_was(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        before = await seed_pre_0006(connection)

        await migrate(WITH_ROOTS)

        rows = await connection.execute(text("SELECT name, folder_id FROM file_item"))
        assert {name: str(folder_id) for name, folder_id in rows} == {
            "brief.pdf": before["architecture"],
            "Tax portal": before["year"],
        }

    async def test_the_index_then_refuses_a_second_root(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)
        await migrate(WITH_ROOTS)

        with pytest.raises(IntegrityError):
            await connection.execute(
                text(
                    "INSERT INTO folder (id, project_id, parent_id, name)"
                    " SELECT gen_random_uuid(), id, NULL, 'Impostor'"
                    " FROM project WHERE key = 'ATL'"
                )
            )

    async def test_works_on_a_database_with_no_projects_at_all(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(WITH_ROOTS)

        assert await connection.scalar(text("SELECT count(*) FROM folder")) == 0


class TestUndoingRootFolders:
    async def test_puts_the_tree_back_as_it_was(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        before = await seed_pre_0006(connection)
        await migrate(WITH_ROOTS)

        await rewind(BEFORE_ROOTS)

        assert await parent_of(connection, before["architecture"]) is None
        assert await parent_of(connection, before["finance"]) is None
        assert await parent_of(connection, before["year"]) == before["finance"]

    async def test_takes_the_roots_away_again(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)
        await migrate(WITH_ROOTS)

        await rewind(BEFORE_ROOTS)

        names = await connection.scalars(text("SELECT name FROM folder ORDER BY name"))
        assert list(names) == ["2025", "Architecture", "Finance inputs", "Hermes Notifications"]

    async def test_refuses_while_a_file_sits_in_a_root(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        """The older shape cannot hold it, and a cascade would delete it."""
        await migrate(BEFORE_ROOTS)
        await seed_pre_0006(connection)
        await migrate(WITH_ROOTS)
        atlas, _ = (await roots(connection))["ATL"]
        await connection.execute(
            text(
                "INSERT INTO file_item (id, folder_id, kind, name, url, source)"
                " VALUES (gen_random_uuid(), :folder, 'link', 'README', 'https://x.example', "
                "'other')"
            ),
            {"folder": atlas},
        )

        with pytest.raises(RuntimeError, match="sit directly in a project root"):
            await rewind(BEFORE_ROOTS)


DATED = "0011"
OPTIONALLY_DATED = "0012"


async def seed_a_task(connection: AsyncConnection, *, due_date: date | None) -> None:
    """One project, one person, one column, one card on it.

    The smallest database revision 0012 has anything to say about: it changes
    exactly one column on exactly one table, and the only question worth asking
    of it is what happens to a card that has no date when the schema goes back
    to demanding one.
    """
    await connection.execute(
        text(
            "INSERT INTO project (id, key, name, description, colour, task_counter)"
            " VALUES (gen_random_uuid(), 'ATL', 'Atlas Billing Migration', '', '#1D7D46', 1)"
        )
    )
    await connection.execute(
        text(
            "INSERT INTO person (id, name, kind, role, responsibilities, colour)"
            " VALUES (gen_random_uuid(), 'Aditi K', 'team', 'Backend engineer', '', '#1D7D46')"
        )
    )
    await connection.execute(
        text(
            "INSERT INTO board_column (id, project_id, name, description, position)"
            " SELECT gen_random_uuid(), project.id, 'To do', '', 0 FROM project"
        )
    )
    await connection.execute(
        text(
            "INSERT INTO task (id, project_id, number, column_id, position, title,"
            " description, type, due_date, assignee_id, status)"
            " SELECT gen_random_uuid(), project.id, 1, board_column.id, 0,"
            " 'Stripe webhook idempotency', 'Dedupe on event id.', 'bug', :due,"
            " person.id, 'active'"
            " FROM project, board_column, person"
        ),
        {"due": due_date},
    )


async def due_dates(connection: AsyncConnection) -> list[Any]:
    return list((await connection.execute(text("SELECT due_date FROM task"))).scalars())


class TestMakingTheDueDateOptional:
    async def test_leaves_the_dates_already_there(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        """Nothing is back-filled and nothing is cleared: every date already on
        a card was a date somebody meant."""
        await migrate(DATED)
        await seed_a_task(connection, due_date=date(2026, 9, 1))

        await migrate(OPTIONALLY_DATED)

        assert [str(due) for due in await due_dates(connection)] == ["2026-09-01"]

    async def test_then_accepts_a_card_with_no_date(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(OPTIONALLY_DATED)

        await seed_a_task(connection, due_date=None)

        assert await due_dates(connection) == [None]


class TestUndoingTheOptionalDueDate:
    async def test_puts_a_dated_card_back_untouched(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(OPTIONALLY_DATED)
        await seed_a_task(connection, due_date=date(2026, 9, 1))

        await rewind(DATED)

        assert [str(due) for due in await due_dates(connection)] == ["2026-09-01"]

    async def test_refuses_while_a_card_has_no_date(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        """Any date it invented would show on the board as a deadline nobody
        chose, so it stops and says so instead."""
        await migrate(OPTIONALLY_DATED)
        await seed_a_task(connection, due_date=None)

        with pytest.raises(RuntimeError, match="have no due date"):
            await rewind(DATED)


# --- A reason long enough for connection_lost ------------------------------

NARROW_REASON = "0021"
WIDE_REASON = "0022"


async def seed_an_agent_session(connection: AsyncConnection, *, reason: str) -> None:
    """One card with one finished agent session on it, under a given reason."""
    await seed_a_task(connection, due_date=None)
    await connection.execute(
        text(
            "INSERT INTO agent_session (id, task_id, token_id, actor_label,"
            " client_session_id, client_name, state, reason, started_at,"
            " state_changed_at, last_seen_at, ended_at)"
            " SELECT gen_random_uuid(), task.id, NULL, 'claude code hook',"
            " 'sess-1', 'ATL-1', 'done', :reason, now(), now(), now(), now()"
            " FROM task"
        ),
        {"reason": reason},
    )


async def reasons(connection: AsyncConnection) -> list[Any]:
    return list((await connection.execute(text("SELECT reason FROM agent_session"))).scalars())


class TestWideningTheReason:
    async def test_refuses_connection_lost_before_the_widen(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        """Why the revision exists at all.

        ``reason`` is a VARCHAR sized to the longest member the schema knew,
        and ``connection_lost`` is two characters past it. Nothing in
        ``alembic check`` compares a column's width, so this is the only place
        the widen is proved.
        """
        await migrate(NARROW_REASON)

        with pytest.raises(DBAPIError, match=r"too long|right truncation"):
            await seed_an_agent_session(connection, reason="connection_lost")

    async def test_accepts_connection_lost_after_it(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(WIDE_REASON)

        await seed_an_agent_session(connection, reason="connection_lost")

        assert await reasons(connection) == ["connection_lost"]

    async def test_leaves_the_reasons_already_written_alone(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
    ) -> None:
        await migrate(NARROW_REASON)
        await seed_an_agent_session(connection, reason="session_ended")

        await migrate(WIDE_REASON)

        assert await reasons(connection) == ["session_ended"]


class TestUndoingTheWidenedReason:
    async def test_puts_a_lost_connection_back_as_an_ended_session(
        self,
        connection: AsyncConnection,
        migrate: Callable[[str], Awaitable[None]],
        rewind: Callable[[str], Awaitable[None]],
    ) -> None:
        """The session is over either way; only the cause does not fit."""
        await migrate(WIDE_REASON)
        await seed_an_agent_session(connection, reason="connection_lost")

        await rewind(NARROW_REASON)

        assert await reasons(connection) == ["session_ended"]
