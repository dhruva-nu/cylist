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
