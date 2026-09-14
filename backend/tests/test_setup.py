"""``GET /setup`` — the addresses a client should keep."""

from __future__ import annotations

from httpx import AsyncClient

from app.config import Settings
from app.db import Database
from tests.conftest import client_for


async def test_needs_no_credentials(client: AsyncClient) -> None:
    """It is read before a client has a token; that is its whole purpose."""
    assert "authorization" not in client.headers
    assert (await client.get("/setup")).status_code == 200


async def test_reports_the_environment_and_the_scopes_an_agent_needs(
    client: AsyncClient,
) -> None:
    body = (await client.get("/setup")).json()

    assert body["environment"] == "test"
    assert body["agent_scopes"] == ["read", "write"]


async def test_urls_are_empty_when_none_are_configured(client: AsyncClient) -> None:
    """The right answer for a server that is only ever reached one way."""
    assert (await client.get("/setup")).json()["urls"] == []


async def test_configured_urls_are_returned_in_order(
    settings: Settings, database: Database
) -> None:
    """Order is the point: it is the order a client will try them in."""
    configured = settings.model_copy(
        update={
            "client_urls": [
                "http://localhost:8000",
                "https://box.tailnet.ts.net",
            ]
        }
    )
    async with client_for(configured, database) as http:
        body = (await http.get("/setup")).json()

    assert body["urls"] == ["http://localhost:8000", "https://box.tailnet.ts.net"]


async def test_urls_are_trimmed_and_deduplicated_without_reordering(
    settings: Settings, database: Database
) -> None:
    configured = settings.model_copy(
        update={
            "client_urls": [
                " http://localhost:8000/ ",
                "",
                "https://box.tailnet.ts.net",
                "http://localhost:8000",
            ]
        }
    )
    async with client_for(configured, database) as http:
        body = (await http.get("/setup")).json()

    assert body["urls"] == ["http://localhost:8000", "https://box.tailnet.ts.net"]
