"""The MCP tools at /mcp, against the real app and a real database.

``cylist_mcp``'s own suite covers the protocol against a fake API. What only
this can show is the wiring: that the route answers beside the SPA, that the
tools reach this app in-process with the caller's token, and that a browser's
session cookie — which carries every scope — is not a way in.
"""

from __future__ import annotations

import base64
import json

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


SKILL_FOLDER = {
    "SKILL.md": b"---\nname: release-kit\n---\n\nRun scripts/cut.sh, then write the notes.\n",
    "scripts/cut.sh": b'#!/bin/sh\ngit tag -a "$1" -m "$1"\n',
    "reference/tone.md": b"# Tone\n\nPlain sentences. No exclamation marks.\n",
    "reference/logo.png": b"\x89PNG\r\n\x1a\n\x00\xff\xfe",
}
"""A skill as skills actually are: instructions, a script to run, and the
references and assets they point at."""


def _payload(result: CallToolResult) -> dict:
    """A tool's own result, parsed out of the text block it comes back in."""
    text = "".join(block.text for block in result.content if isinstance(block, TextContent))
    found = json.loads(text)
    assert isinstance(found, dict)
    return found


async def test_a_folder_of_a_skill_round_trips_through_the_tools(
    signed_in: AsyncClient,
) -> None:
    """The card: upload a whole skill folder, get every file back through MCP."""
    await signed_in.post("/projects", json={"key": "ATL", "name": "Atlas"})
    uploaded = await signed_in.post(
        "/projects/ATL/skills",
        files=[
            ("file", (f"release-kit/{path}", data, "application/octet-stream"))
            for path, data in SKILL_FOLDER.items()
        ],
        data={
            "folder": "release-kit",
            "description": "Cut a release.",
            "executable": "release-kit/scripts/cut.sh",
        },
    )
    assert uploaded.status_code == 201, uploaded.text

    token = await _token(signed_in, ["read", "write"])
    app = signed_in.app  # type: ignore[attr-defined]
    async with (
        app.state.mcp.run(),
        _http(signed_in, Authorization=f"Bearer {token}") as http,
        Client(streamable_http_client(MCP_URL, http_client=http), mode="legacy") as mcp,
    ):
        listed = _payload(await mcp.call_tool("list_skills", {"project": "ATL"}))
        read = _payload(
            await mcp.call_tool("read_skill", {"project": "ATL", "name": "release-kit.zip"})
        )
        downloaded = _payload(
            await mcp.call_tool("download_skill", {"project": "ATL", "name": "release-kit.zip"})
        )

    assert [one["name"] for one in listed["skills"]] == ["release-kit.zip"]
    assert "Run scripts/cut.sh" in read["content"], "the SKILL.md, not the zip's bytes"
    assert read["files"] == [
        "SKILL.md",
        "reference/logo.png",
        "reference/tone.md",
        "scripts/cut.sh",
    ]

    assert downloaded["folder"] == "release-kit"
    assert downloaded["install"]["directory"] == ".claude/skills/release-kit/"
    came_back = {
        one["path"]: (
            base64.b64decode(one["content"])
            if one["encoding"] == "base64"
            else one["content"].encode()
        )
        for one in downloaded["files"]
    }
    manifest = came_back.pop("SKILL.md").decode()
    assert manifest == (
        '---\ndescription: "Cut a release."\nname: release-kit\n---\n\n'
        "Run scripts/cut.sh, then write the notes.\n"
    ), "the SKILL.md as written, with the description Claude Code reads filled in"
    assert came_back == {path: data for path, data in SKILL_FOLDER.items() if path != "SKILL.md"}, (
        "every other file byte for byte"
    )
    runnable = {one["path"] for one in downloaded["files"] if one["executable"]}
    assert runnable == {"scripts/cut.sh"}


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
