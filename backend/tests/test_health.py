"""The health endpoint."""

from __future__ import annotations

from httpx import AsyncClient


async def test_reports_ok_when_the_database_is_reachable(client: AsyncClient) -> None:
    response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "database": "up"}


async def test_needs_no_credentials(client: AsyncClient) -> None:
    """An orchestrator must be able to poll it without a token."""
    assert "authorization" not in client.headers
    assert (await client.get("/health")).status_code == 200
