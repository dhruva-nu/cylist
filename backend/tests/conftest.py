"""Test fixtures.

Tests run against a real PostgreSQL instance. The schema uses ARRAY, JSONB and
timezone-aware timestamps, so testing on SQLite would be testing something
Cylist never runs on.

Which instance is used:

* ``CYLIST_TEST_DATABASE_URL`` set — that database. CI points this at its
  service container; set it yourself to reuse ``make db``.
* unset — an embedded PostgreSQL, started for the run and thrown away
  afterwards, so ``make test`` needs no Docker and no setup.

Each test gets a clean schema: tables are created once per session from the ORM
metadata and truncated between tests, which is far quicker than re-running
migrations for every test. ``make migrate-check`` separately proves the
migrations produce that same schema.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.config import Settings
from app.db import Database
from app.main import create_app
from app.models import Base

OWNER_PASSWORD = "correct-horse-battery-staple"

TEST_DATABASE_NAME = "cylist_test"


@pytest.fixture(scope="session")
def database_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """The database to test against; embedded unless one is configured."""
    configured = os.environ.get("CYLIST_TEST_DATABASE_URL")
    if configured:
        yield configured
        return

    try:
        import pgserver
    except ModuleNotFoundError:  # pragma: no cover - only when deps are partial
        pytest.skip(
            "No CYLIST_TEST_DATABASE_URL, and pgserver is not installed. "
            "Run 'uv sync' or start a database with 'make db'."
        )

    server = pgserver.get_server(tmp_path_factory.mktemp("pgdata"))
    server.psql(f"CREATE DATABASE {TEST_DATABASE_NAME};")
    try:
        # pgserver hands back a libpq URI over a unix socket; SQLAlchemy needs
        # the driver named in the scheme.
        uri = server.get_uri(database=TEST_DATABASE_NAME)
        yield uri.replace("postgresql://", "postgresql+asyncpg://", 1)
    finally:
        server.cleanup()


@pytest.fixture(scope="session")
def settings(tmp_path_factory: pytest.TempPathFactory, database_url: str) -> Settings:
    """Configuration pointed at the test database and a temporary data dir."""
    return Settings(
        environment="test",
        database_url=database_url,
        data_dir=tmp_path_factory.mktemp("cylist-data"),
        password_hash=hash_password(OWNER_PASSWORD),
        cors_origins=[],
    )


@pytest.fixture(scope="session")
async def database(settings: Settings) -> AsyncIterator[Database]:
    """Create the schema once, drop it at the end of the run."""
    db = Database(settings.database_url)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield db
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await db.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(database: Database) -> AsyncIterator[None]:
    """Empty every table before each test so order never matters."""
    tables = ", ".join(f'"{table.name}"' for table in reversed(Base.metadata.sorted_tables))
    async with database.engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session for tests that talk to the database directly."""
    async with database.session() as db_session:
        yield db_session


@asynccontextmanager
async def client_for(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to an app built with these settings.

    The app is built without its lifespan so it reuses the session-scoped
    engine instead of opening a second pool per test. Exposed rather than
    inlined into the fixture because a test that needs different configuration
    — a smaller upload cap, say — has to build its own app to get it.
    """
    app = create_app(settings)
    app.state.settings = settings
    app.state.database = database

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
        yield http


async def sign_in(client: AsyncClient) -> AsyncClient:
    """Give a client the owner's session cookie."""
    response = await client.post("/auth/login", json={"password": OWNER_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
async def client(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to the app, sharing the test database."""
    async with client_for(settings, database) as http:
        yield http


@pytest.fixture
async def signed_in(client: AsyncClient) -> AsyncClient:
    """A client holding a valid owner session cookie."""
    return await sign_in(client)
