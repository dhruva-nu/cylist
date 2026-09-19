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
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.passwords import hash_password
from app.config import Settings
from app.core.palette import colour_for
from app.db import Database
from app.main import create_app
from app.models import Base
from app.models.person import Person, PersonKind

OWNER_PASSWORD = "correct-horse-battery-staple"
INVITEE_PASSWORD = "sixteen-horses-and-one-stapler"
"""What everybody invited during a test sets their password to."""

OWNER_EMAIL = "dhruva@cylist.dev"
OWNER_NAME = "Dhruva N"

OWNER_PASSWORD_HASH = hash_password(OWNER_PASSWORD)
"""Hashed once for the whole run.

Argon2 is deliberately slow, and almost every test in the suite signs in.
Hashing per test would add a large multiple of the suite's own runtime to
prove something one test already proves.
"""

VAULT_KEY = "dGVzdC12YXVsdC1rZXktMzItYnl0ZXMtZXhhY3RseSE="
"""A fixed 32-byte AES key, so a ciphertext written by one test is readable by
the next. Never used anywhere but here; a real one comes from
``python -m app.cli generate-vault-key``."""

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
        vault_key=VAULT_KEY,
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
    # The lifespan would have done this; without it the shared pool has
    # nobody to tell about a commit. Rebound per test, so each app's sockets
    # hear only their own test's writes.
    database.publish_to(app.state.hub.publish)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test/api/v1") as http:
            http.app = app  # type: ignore[attr-defined]
            yield http
    finally:
        database.publish_to(None)


async def ensure_owner(client: AsyncClient) -> Person:
    """Put an account in the directory for tests to sign in as.

    Written straight to the database rather than through invite-and-accept.
    That path has tests of its own; running it in front of every other test
    would mean two more Argon2 hashes per test to arrive at a row this can
    insert in one statement.

    The tables are truncated between tests, so this runs once per test and
    finds nothing there. It still checks: a test that signs two clients in is
    asking for one account, not two.
    """
    database: Database = client.app.state.database  # type: ignore[attr-defined]
    async with database.session() as db_session:
        existing = await db_session.scalar(
            select(Person).where(func.lower(Person.email) == OWNER_EMAIL)
        )
        if existing is not None:
            return existing
        person = Person(
            name=OWNER_NAME,
            kind=PersonKind.TEAM,
            title="Tech lead",
            responsibilities="Owns the architecture and the cutover plan.",
            email=OWNER_EMAIL,
            colour=colour_for(OWNER_NAME),
            password_hash=OWNER_PASSWORD_HASH,
        )
        db_session.add(person)
        await db_session.flush()
        await db_session.refresh(person)
        return person


async def sign_in(client: AsyncClient) -> AsyncClient:
    """Give a client a session cookie belonging to a real person.

    The ordinary case, and so the default: almost everything in Cylist now
    happens as somebody rather than as the deployment. Tests about the
    bootstrap login — the one that belongs to nobody — use
    :func:`bootstrap_sign_in` instead.
    """
    await ensure_owner(client)
    response = await client.post(
        "/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert response.status_code == 200, response.text
    return client


async def bootstrap_sign_in(client: AsyncClient) -> AsyncClient:
    """Sign in with ``CYLIST_PASSWORD_HASH``, as a fresh deployment does.

    Only works while the directory holds nobody who can sign in, which is the
    whole point of it.
    """
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
    """A client signed in as a real person, with every scope."""
    return await sign_in(client)


@pytest.fixture
async def owner(signed_in: AsyncClient) -> Person:
    """The person ``signed_in`` is signed in as."""
    return await ensure_owner(signed_in)


@pytest.fixture
async def other_client(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """A second browser against the same deployment, signed in as nobody yet.

    Not ``client``: that fixture and ``signed_in`` are the same object, so a
    test that signed a second person in through it would be replacing the
    first one's cookie rather than holding two. Which is fine until the test
    goes on to use the first — and then fails somewhere that looks nothing
    like the mistake.
    """
    async with client_for(settings, database) as http:
        yield http


@pytest.fixture
async def bootstrapped(client: AsyncClient) -> AsyncClient:
    """A client holding the bootstrap session of an account-less deployment."""
    return await bootstrap_sign_in(client)


async def open_account(
    admin: AsyncClient, person: dict[str, object], email: str
) -> tuple[dict, str]:
    """Add somebody to the directory and invite them, as an admin would.

    Returns the created person and the one-time invitation token. Stops short
    of accepting it, because which client accepts decides whose browser ends
    up signed in — and the tests that care about more than one person signed
    in at once care about exactly that.
    """
    created = (await admin.post("/people", json={**person, "email": email})).json()
    issued = await admin.post(f"/people/{created['id']}/invite")
    assert issued.status_code == 201, issued.text
    return created, issued.json()["token"]
