"""The MCP tools at /mcp, against the real app and a real database.

``cylist_mcp``'s own suite covers the protocol against a fake API. What only
this can show is the wiring: that the route answers beside the SPA, that the
tools reach this app in-process with the caller's token, and that a browser's
session cookie — which carries every scope — is not a way in.
"""

from __future__ import annotations

import httpx2
from httpx import AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent

MCP_URL = "http://test/mcp"


async def _token(signed_in: AsyncClient, scopes: list[str]) -> str:
    response = await signed_in.post("/tokens", json={"name": "Claude Code", "scopes": scopes})
    assert response.status_code == 201
    token: str = response.json()["token"]
    return token


def _http(signed_in: AsyncClient, **headers: str) -> httpx2.AsyncClient:
    app = signed_in.app  # type: ignore[attr-defined]
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), headers=headers)


async def test_a_tool_reads_the_board_as_the_token_holder(signed_in: AsyncClient) -> None:
    await signed_in.post("/projects", json={"key": "ATL", "name": "Atlas"})
    token = await _token(signed_in, ["read", "write"])
    app = signed_in.app  # type: ignore[attr-defined]

    async with (
        app.state.mcp.run(),
        _http(signed_in, Authorization=f"Bearer {token}") as http,
        Client(streamable_http_client(MCP_URL, http_client=http), mode="legacy") as mcp,
    ):
        created = await mcp.call_tool(
            "create_task",
            {
                "project": "ATL",
                "title": "Wire up hosted MCP",
                "description": "One line connects a machine.",
                "task_type": "feature",
            },
        )
        listed = await mcp.call_tool("list_tasks", {"project": "ATL"})

    assert isinstance(created, CallToolResult)
    assert not created.is_error, created.content
    assert isinstance(listed, CallToolResult)
    text = "".join(block.text for block in listed.content if isinstance(block, TextContent))
    assert "Wire up hosted MCP" in text

    # An agent's work, in the owner's name — as it is over plain HTTP.
    feed = (await signed_in.get("/activity", params={"project": "ATL"})).json()
    made = next(entry for entry in feed if entry["verb"] == "task.created")
    assert made["channel"] == "api"


async def test_a_session_cookie_is_not_a_way_in(signed_in: AsyncClient) -> None:
    """Signed in to the board is not the same as holding a token."""
    app = signed_in.app  # type: ignore[attr-defined]
    cookies = dict(signed_in.cookies.items())
    assert cookies, "the fixture should be holding a session cookie"

    async with app.state.mcp.run(), _http(signed_in) as http:
        http.cookies.update(cookies)
        response = await http.post(
            MCP_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers={"Accept": "application/json, text/event-stream"},
        )

    assert response.status_code == 401


async def test_mcp_is_not_part_of_the_rest_contract(client: AsyncClient) -> None:
    paths = (await client.get("/openapi.json")).json()["paths"]
    assert not any("mcp" in path for path in paths)
