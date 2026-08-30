"""Starting the server on stdio.

Startup does one network call before it serves anything: ``GET /me``, to learn
which scopes the configured token holds. That answers two questions at once —
is this token usable at all, and should ``reveal_secret`` exist — and it is
much better to fail here, where the MCP client shows the error, than to fail
later inside a tool call where only the model sees it.
"""

from __future__ import annotations

import asyncio
import sys

from cylist_mcp.client import ApiClient
from cylist_mcp.config import from_environment
from cylist_mcp.errors import CylistError
from cylist_mcp.server import build_server


async def scopes_of(client: ApiClient) -> frozenset[str]:
    """Which scopes the configured token carries, per the server."""
    identity = await client.get("/me")
    scopes = identity.get("scopes", [])
    return frozenset(str(scope) for scope in scopes)


async def serve() -> None:
    settings = from_environment()
    client = ApiClient(settings.url, settings.token)
    try:
        scopes = await scopes_of(client)
        # stderr, never stdout: stdout is the JSON-RPC channel and anything
        # else written there corrupts the protocol.
        print(
            f"cylist-mcp: connected to {settings.url} with scopes "
            f"{', '.join(sorted(scopes)) or 'none'}.",
            file=sys.stderr,
        )
        await build_server(client, scopes).run_stdio_async()
    finally:
        await client.aclose()


def run() -> None:
    """Console-script entry point."""
    try:
        asyncio.run(serve())
    except CylistError as exc:
        print(f"cylist-mcp: {exc.message}", file=sys.stderr)
        raise SystemExit(1) from exc


if __name__ == "__main__":
    run()
