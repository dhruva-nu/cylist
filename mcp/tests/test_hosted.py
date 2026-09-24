"""The tools served over HTTP: the credential check, and a real client's view.

The client here is the SDK's own streamable-HTTP client, so these tests speak
the protocol the way Claude Code does rather than hand-rolling JSON-RPC — and
both handshake eras are run, because which one a client uses is its choice.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import anyio
import httpx
import httpx2
import pytest
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

from cylist_mcp.hosted import HostedMcp
from tests import fake_api

URL = "http://cylist.test/mcp"

MODES = ["legacy", "auto"]


def _api(recorder: fake_api.Recorder, tokens: dict[str, list[str]]) -> httpx.MockTransport:
    """The fake API, answering each token with that token's own scopes."""

    def handler(request: httpx.Request) -> httpx.Response:
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        if token not in tokens:
            recorder.requests.append(request)
            return httpx.Response(
                401, json={"error": {"code": "unauthenticated", "message": "Invalid token."}}
            )
        inner = fake_api.build(recorder, scopes=tokens[token])
        return inner.handle_request(request)

    return httpx.MockTransport(handler)


@pytest.fixture
def hosted(recorder: fake_api.Recorder) -> HostedMcp:
    return HostedMcp(
        _api(
            recorder,
            {"cyl_board": ["read", "write"], "cyl_vault": ["read", "write", "vault:reveal"]},
        )
    )


def _http(hosted: HostedMcp, token: str | None) -> httpx2.AsyncClient:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=hosted), headers=headers)


@asynccontextmanager
async def _connected(hosted: HostedMcp, token: str, mode: str) -> AsyncIterator[Client]:
    async with (
        hosted.run(),
        _http(hosted, token) as http,
        Client(streamable_http_client(URL, http_client=http), mode=mode) as client,
    ):
        yield client


def _text(result: Any) -> str:
    assert isinstance(result, CallToolResult)
    return "\n".join(block.text for block in result.content if isinstance(block, TextContent))


async def test_a_request_without_a_token_is_refused_before_mcp(hosted: HostedMcp) -> None:
    async with hosted.run(), _http(hosted, None) as http:
        response = await http.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert "Agents page" in response.json()["error"]["message"]


async def test_only_post_is_served(hosted: HostedMcp) -> None:
    """No session and nothing to push: a GET stream would only ever idle."""
    async with hosted.run(), _http(hosted, "cyl_board") as http:
        response = await http.get(URL)

    assert response.status_code == 405
    assert response.headers["allow"] == "POST"


async def test_a_token_the_api_rejects_is_refused(
    hosted: HostedMcp, recorder: fake_api.Recorder
) -> None:
    async with hosted.run(), _http(hosted, "cyl_revoked") as http:
        response = await http.post(URL, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    assert response.status_code == 401
    assert recorder.paths() == ["GET /api/v1/me"]


@pytest.mark.parametrize("mode", MODES)
async def test_a_tool_call_carries_the_callers_own_token(
    hosted: HostedMcp, recorder: fake_api.Recorder, mode: str
) -> None:
    """The API's scope checks stay the only ones: the tool acts as the caller."""
    async with _connected(hosted, "cyl_board", mode) as client:
        result = await client.call_tool("list_projects", {})

    assert '"key": "ATL"' in _text(result)
    sent = recorder.sent("GET", "/projects")
    assert sent.headers["authorization"] == "Bearer cyl_board"


@pytest.mark.parametrize("mode", MODES)
async def test_reveal_secret_is_offered_only_to_a_token_that_can_use_it(
    hosted: HostedMcp, mode: str
) -> None:
    """One process, every token: the choice is made per request, not at startup."""

    async def names(token: str) -> set[str]:
        async with (
            _http(hosted, token) as http,
            Client(streamable_http_client(URL, http_client=http), mode=mode) as client,
        ):
            return {tool.name for tool in (await client.list_tools()).tools}

    async with hosted.run():
        board = await names("cyl_board")
        vault = await names("cyl_vault")

    assert "list_projects" in board
    assert "reveal_secret" not in board
    assert "reveal_secret" in vault


async def test_two_callers_at_once_each_get_their_own_token(
    hosted: HostedMcp, recorder: fake_api.Recorder
) -> None:
    """One process serves every token; a request must never borrow another's."""
    async with hosted.run():

        async def use(token: str) -> None:
            async with (
                _http(hosted, token) as http,
                Client(streamable_http_client(URL, http_client=http), mode="legacy") as client,
            ):
                await client.call_tool("list_projects", {})

        async with anyio.create_task_group() as group:
            group.start_soon(use, "cyl_board")
            group.start_soon(use, "cyl_vault")

    tokens = sorted(
        request.headers["authorization"]
        for request in recorder.requests
        if request.url.path.endswith("/projects")
    )
    assert tokens == ["Bearer cyl_board", "Bearer cyl_vault"]


async def test_the_day_report_does_not_default_to_the_servers_own_zone(
    hosted: HostedMcp, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    """On the server, "this machine" is a container: the API's UTC is more honest."""
    monkeypatch.setenv("TZ", "Europe/Berlin")

    async with _connected(hosted, "cyl_board", "legacy") as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        await client.call_tool("day_report", {"project": "ATL"})

    assert "this machine" not in (tools["day_report"].description or "")
    assert "timezone" not in recorder.sent("GET", "/reports/day").url.params
