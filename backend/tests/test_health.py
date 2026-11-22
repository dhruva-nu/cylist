"""The health endpoint."""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from httpx import AsyncClient

from app.config import Settings
from app.db import Database
from tests.conftest import as_new, client_on


async def test_reports_ok_when_the_database_is_reachable(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


@asynccontextmanager
async def _client_against(
    shared_app: FastAPI, settings: Settings, database: Database, port: int
) -> AsyncIterator[AsyncClient]:
    """The app, with its pool pointed at 127.0.0.1:``port`` instead."""
    url = settings.database_url.split("@", 1)[0] + f"@127.0.0.1:{port}/cylist"
    unreachable = Database(url, pool_timeout=2.0)
    app = as_new(shared_app, settings, database)
    app.state.database = unreachable
    try:
        async with client_on(app) as http:
            yield http
    finally:
        app.state.database = database
        await unreachable.dispose()


async def test_reports_degraded_when_nothing_listens(
    shared_app: FastAPI, settings: Settings, database: Database
) -> None:
    """Postgres's pod gone: the connection is refused before any SQL is sent."""
    async with _client_against(shared_app, settings, database, _free_port()) as http:
        response = await http.get("/health")

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}


async def test_reports_degraded_while_postgres_shuts_down(
    shared_app: FastAPI, settings: Settings, database: Database
) -> None:
    """Postgres answering, but refusing new connections with SQLSTATE 57P03.

    What a deleted Postgres pod says for its last few seconds. asyncpg raises
    this from connect, where SQLAlchemy does not wrap it; seen as a 500 in the
    K3s pod-kill drill.
    """

    async def refuse(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # An SSLRequest first, unless the client was told not to: decline it,
        # then read the startup message that follows.
        length = struct.unpack("!I", await reader.readexactly(4))[0]
        body = await reader.readexactly(length - 4)
        if body[:4] == struct.pack("!I", 80877103):
            writer.write(b"N")
            length = struct.unpack("!I", await reader.readexactly(4))[0]
            await reader.readexactly(length - 4)
        fields = b"".join(
            code + text + b"\0"
            for code, text in [
                (b"S", b"FATAL"),
                (b"V", b"FATAL"),
                (b"C", b"57P03"),
                (b"M", b"the database system is shutting down"),
            ]
        )
        writer.write(b"E" + struct.pack("!I", len(fields) + 5) + fields + b"\0")
        await writer.drain()
        writer.close()

    server = await asyncio.start_server(refuse, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    try:
        async with _client_against(shared_app, settings, database, port) as http:
            response = await http.get("/health")
    finally:
        server.close()
        await server.wait_closed()

    assert response.status_code == 503
    assert response.json() == {"status": "degraded", "database": "down"}


async def test_needs_no_credentials(client: AsyncClient) -> None:
    """An orchestrator must be able to poll it without a token."""
    assert "authorization" not in client.headers
    assert (await client.get("/health")).status_code == 200


async def test_request_counts_needs_no_credentials(client: AsyncClient) -> None:
    assert (await client.get("/health/requests")).status_code == 200


async def test_request_counts_reflects_prior_requests(client: AsyncClient) -> None:
    await client.get("/health")
    await client.get("/nonexistent-route")

    body = (await client.get("/health/requests")).json()

    # Not >= 3: the middleware records a response's status after the handler
    # returns it, so this request's own count is not yet reflected in the body
    # it returns.
    assert body["total"] >= 2
    assert body["status_2xx"] >= 1
    assert body["status_4xx"] >= 1
