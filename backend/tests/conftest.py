"""Test fixtures.

Tests run against a real PostgreSQL instance. The schema uses ARRAY, JSONB and
timezone-aware timestamps, so testing on SQLite would be testing something
Cylist never runs on.

Which instance is used:

* ``CYLIST_TEST_DATABASE_URL`` set — that database. CI points this at its
  service container; set it yourself to reuse ``make db``.
* unset — an embedded PostgreSQL, started for the run and thrown away
  afterwards, so ``make test`` needs no Docker and no setup.

Run under pytest-xdist (``-n auto``, as CI and ``make test`` do), each worker
is a process of its own with a database of its own: a configured server gets
one database per worker, named after it, and the embedded case one server per
worker. Truncating between tests is only safe because nobody else is using the
tables.

Each test gets a clean schema: tables are created once per session from the ORM
metadata and truncated between tests, which is far quicker than re-running
migrations for every test. ``make migrate-check`` separately proves the
migrations produce that same schema.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

import pytest
from argon2 import PasswordHasher, profiles
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.auth import passwords
from app.auth.passwords import hash_password_sync
from app.auth.throttle import SignInThrottle
from app.config import Settings
from app.core.palette import colour_for
from app.db import Database
from app.main import create_app
from app.models import Base
from app.models.person import Person, PersonKind
from app.realtime.bus import Bus
from app.realtime.hub import Hub
from app.services import blobs
from app.services.doc_engine import engine_for
from app.services.docs import CorpusCache
from app.services.hook_delivery import Courier
from app.services.push.delivery import Pusher
from app.storage import LocalBlobStore

# Before anything below hashes a password. The deployed parameters cost ~70 ms
# a hash or a check, and the suite does one or more of those in nearly every
# test — through sign-in, invitations and the bootstrap login — which added up
# to over a minute of the serial run. What the tests prove about hashing (salted, the
# wrong password refused, a malformed hash refused) holds at any cost; the
# parameters themselves are argon2-cffi's defaults, which are its business.
passwords._hasher = PasswordHasher.from_parameters(profiles.CHEAPEST)

OWNER_PASSWORD = "correct-horse-battery-staple"
INVITEE_PASSWORD = "sixteen-horses-and-one-stapler"
"""What everybody invited during a test sets their password to."""

OWNER_EMAIL = "dhruva@cylist.dev"
OWNER_NAME = "Dhruva N"

OWNER_PASSWORD_HASH = hash_password_sync(OWNER_PASSWORD)
"""Hashed once for the whole run, rather than once per test that signs in."""

VAULT_KEY = "dGVzdC12YXVsdC1rZXktMzItYnl0ZXMtZXhhY3RseSE="
"""A fixed 32-byte AES key, so a ciphertext written by one test is readable by
the next. Never used anywhere but here; a real one comes from
``python -m app.cli generate-vault-key``."""

VAULT_KEYS = ((1, VAULT_KEY),)
"""The keyring :data:`VAULT_KEY` alone makes: version 1, as in every deployment."""

TEST_DATABASE_NAME = "cylist_test"

XDIST_WORKER = os.environ.get("PYTEST_XDIST_WORKER")
"""``gw0``, ``gw1``… in a pytest-xdist worker; ``None`` in a run without one."""


def per_worker(name: str) -> str:
    """A database name no other worker of this run is using."""
    return f"{name}_{XDIST_WORKER}" if XDIST_WORKER else name


async def _on_maintenance_db(url: URL, statements: list[str]) -> None:
    """Run statements against the server's ``postgres`` database.

    CREATE and DROP DATABASE cannot run inside a transaction, hence AUTOCOMMIT.
    """
    engine = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.exec_driver_sql(statement)
    finally:
        await engine.dispose()


@pytest.fixture(scope="session")
async def database_url(tmp_path_factory: pytest.TempPathFactory) -> AsyncIterator[str]:
    """The database to test against; embedded unless one is configured."""
    configured = os.environ.get("CYLIST_TEST_DATABASE_URL")
    if configured and not XDIST_WORKER:
        yield configured
        return
    if configured:
        # The configured database names the run; each worker takes one of its
        # own beside it, on the same server, for as long as the run lasts.
        base = make_url(configured)
        name = per_worker(base.database or TEST_DATABASE_NAME)
        await _on_maintenance_db(
            base, [f'DROP DATABASE IF EXISTS "{name}"', f'CREATE DATABASE "{name}"']
        )
        try:
            yield base.set(database=name).render_as_string(hide_password=False)
        finally:
            await _on_maintenance_db(base, [f'DROP DATABASE IF EXISTS "{name}"'])
        return

    try:
        import pgserver
    except ModuleNotFoundError:  # pragma: no cover - only when deps are partial
        pytest.skip(
            "No CYLIST_TEST_DATABASE_URL, and pgserver is not installed. "
            "Run 'uv sync' or start a database with 'make db'."
        )

    # tmp_path_factory is per worker, so under xdist this is a server each.
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
        password_hash=OWNER_PASSWORD_HASH,
        vault_key=VAULT_KEY,
        cors_origins=[],
    )


@pytest.fixture(scope="session")
async def database(settings: Settings) -> AsyncIterator[Database]:
    """Create the schema once, drop it at the end of the run."""
    db = Database.configured(settings)
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    yield db
    async with db.engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
    await db.dispose()


@pytest.fixture(autouse=True)
async def clean_tables(database: Database) -> AsyncIterator[None]:
    """Empty every table before each test so order never matters.

    DELETE rather than TRUNCATE: a test leaves a handful of rows behind, and
    TRUNCATE replaces every table's files whether it held any or not, which
    cost more than the deleting. Children before parents, so no foreign key
    is left pointing at a deleted row.
    """
    async with database.engine.begin() as connection:
        for table in reversed(Base.metadata.sorted_tables):
            await connection.execute(table.delete())
    yield


@pytest.fixture
async def session(database: Database) -> AsyncIterator[AsyncSession]:
    """A session for tests that talk to the database directly."""
    async with database.session() as db_session:
        yield db_session


APP_STATE = {
    "settings",
    "hub",
    "bus",
    "courier",
    "hook_transport",
    "pusher",
    "push_sender",
    "doc_engine",
    "doc_corpora",
    "metrics",
    "mcp",
    "sign_in_throttle",
}
"""Everything ``create_app`` hangs on ``app.state``, which :func:`as_new` must
know how to put back. Checked, so a new one cannot be added and quietly carry
one test's leftovers into the next."""


def as_new(app: FastAPI, settings: Settings, database: Database) -> FastAPI:
    """Give an already-built app the state a freshly built one would have.

    What the session-scoped ``app`` fixture is for. Building an app is cheap,
    but FastAPI works out each route's dependencies and response models the
    first time an app routes a request — some 80 ms for this one — and keeps
    the result on that app. A new app per test paid it in every test, which was
    most of the suite's time.
    """
    # Less the database, which is put there by whoever skips the lifespan.
    built = set(app.state._state) - {"database"}
    assert built == APP_STATE, (
        f"create_app sets {sorted(built ^ APP_STATE)} on app.state; "
        "teach as_new() to reset it and add it to APP_STATE"
    )
    app.state.settings = settings
    app.state.database = database
    app.state.hub = Hub()
    app.state.bus = Bus(app.state.hub)
    app.state.courier = Courier()
    app.state.hook_transport = None
    app.state.pusher = Pusher()
    app.state.push_sender = None
    app.state.doc_engine = engine_for(settings)
    app.state.doc_corpora = CorpusCache()
    # Fresh buckets, so one test's sign-in attempts are not another's.
    app.state.sign_in_throttle = SignInThrottle()
    # In place, not replaced: MetricsMiddleware holds this object.
    app.state.metrics.__init__()
    return app


@pytest.fixture(scope="session")
def shared_app(settings: Settings) -> FastAPI:
    """One app for every test of a worker that uses the default settings."""
    return create_app(settings)


@asynccontextmanager
async def client_for(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to an app built with these settings.

    The app is built without its lifespan so it reuses the session-scoped
    engine instead of opening a second pool per test. Exposed rather than
    inlined into the fixture because a test that needs different configuration
    — a smaller upload cap, say — has to build its own app to get it.
    """
    async with client_on(as_new(create_app(settings), settings, database)) as http:
        yield http


@asynccontextmanager
async def client_on(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to an app that :func:`as_new` has prepared."""
    database: Database = app.state.database
    # The lifespan would have done this; without it the shared pool has
    # nobody to tell about a commit. Rebound per test, so each app's sockets
    # hear only their own test's writes.
    database.publish_to(app.state.bus.publish)

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


async def sweep_blobs(database: Database, settings: Settings) -> blobs.Swept:
    """Run the blob sweep now, with no idle margin.

    A delete takes rows only; the bytes leave at the next sweep (CYLIST-129).
    A test asking whether content left the disk has to run that sweep first.
    """
    return await blobs.sweep(database, LocalBlobStore(settings.blob_dir), idle_for=timedelta(0))


async def bootstrap_sign_in(client: AsyncClient) -> AsyncClient:
    """Sign in with ``CYLIST_PASSWORD_HASH``, as a fresh deployment does.

    Only works while the directory holds nobody who can sign in, which is the
    whole point of it.
    """
    response = await client.post("/auth/login", json={"password": OWNER_PASSWORD})
    assert response.status_code == 200, response.text
    return client


@pytest.fixture
def app(shared_app: FastAPI, settings: Settings, database: Database) -> FastAPI:
    """The worker's app, reset to how a new deployment starts."""
    return as_new(shared_app, settings, database)


@pytest.fixture
async def client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """An HTTP client wired to the app, sharing the test database."""
    async with client_on(app) as http:
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
async def other_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """A second browser against the same deployment, signed in as nobody yet.

    Not ``client``: that fixture and ``signed_in`` are the same object, so a
    test that signed a second person in through it would be replacing the
    first one's cookie rather than holding two. Which is fine until the test
    goes on to use the first — and then fails somewhere that looks nothing
    like the mistake.
    """
    async with client_on(app) as http:
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


async def put_members(client: AsyncClient, project: str, person_ids: list[str]) -> Response:
    """Put these people on a project, keeping whoever is asking on it too.

    ``PUT /members`` replaces the list, and somebody who leaves a project can
    no longer see it — so a test staffing a board with the people its cards
    need would otherwise be locking its own client out of the board. The
    bootstrap session is on no project and needs no keeping.
    """
    me = (await client.get("/me")).json().get("person")
    keep = [me["id"]] if me is not None and me["id"] not in person_ids else []
    return await client.put(f"/projects/{project}/members", json={"person_ids": keep + person_ids})
