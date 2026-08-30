"""Fixtures: a server wired to the fake API, and a way to call one tool."""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent

from cylist_mcp.client import ApiClient
from cylist_mcp.server import build_server
from tests import fake_api


@dataclass
class ToolResult:
    """What a tool call produced, unpacked for assertion."""

    is_error: bool
    text: str
    data: dict[str, Any]


Caller = Callable[..., Awaitable[ToolResult]]


@pytest.fixture
def recorder() -> fake_api.Recorder:
    return fake_api.Recorder()


@pytest_asyncio.fixture
async def make_server(
    recorder: fake_api.Recorder,
) -> AsyncIterator[Callable[..., MCPServer]]:
    """Build a server with a chosen set of scopes and API behaviour."""
    clients: list[ApiClient] = []

    def build(
        *,
        scopes: tuple[str, ...] = ("read", "write"),
        overrides: dict[tuple[str, str], httpx.Response] | None = None,
    ) -> MCPServer:
        transport = fake_api.build(recorder, scopes=list(scopes), overrides=overrides)
        client = ApiClient("http://cylist.test", "cyl_test_token", transport=transport)
        clients.append(client)
        return build_server(client, frozenset(scopes))

    yield build

    for client in clients:
        await client.aclose()


@pytest_asyncio.fixture
async def server(make_server: Callable[..., MCPServer]) -> MCPServer:
    """The common case: a read/write token, no vault access."""
    return make_server()


async def call(server: MCPServer, tool: str, /, **arguments: Any) -> ToolResult:
    """Call one tool and unpack its result.

    Positional-only up to ``tool`` so that a tool argument genuinely called
    ``name`` — ``add_link`` has one — does not collide with this signature.
    """
    result = await server.call_tool(tool, arguments)
    # call_tool can also return InputRequiredResult, for tools that elicit
    # input from the user. None of ours do, and one appearing would be a bug
    # worth failing on rather than quietly unpacking.
    assert isinstance(result, CallToolResult)

    text = "\n".join(block.text for block in result.content if isinstance(block, TextContent))
    data = result.structured_content or {}
    return ToolResult(is_error=bool(result.is_error), text=text, data=data)
