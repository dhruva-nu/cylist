"""The MCP tools, served at ``/mcp`` by this process.

This is what lets a machine connect an agent with one line and nothing
installed::

    claude mcp add --transport http cylist https://<host>/mcp --scope user \\
      --header "Authorization: Bearer cyl_..."

The Agents page mints the token and shows that line with it filled in.

The tools are ``cylist_mcp``'s — the same package the stdio server is, taken
from ``../mcp`` as a path dependency — so there is one set of tool
definitions, not a second copy to keep in step. They reach the API through an
``httpx.ASGITransport`` over this app: in-process, with no socket and no
address to get wrong, and through the full middleware stack, so a tool call
is logged, counted and authorised exactly as the same call over the network
would be. :mod:`cylist_mcp.hosted` says how a request's own token is what
every one of those calls carries.

Beside ``/api/v1`` rather than under it, because it is not a REST endpoint
and the OpenAPI document should not describe it; and on the same funnel, so
it is reachable wherever production is — off the tailnet included.
"""

from __future__ import annotations

import httpx
from cylist_mcp.hosted import HostedMcp
from fastapi import FastAPI
from starlette.routing import Route

MCP_PATH = "/mcp"


def mount_mcp(app: FastAPI) -> HostedMcp:
    """Serve the tools at :data:`MCP_PATH`, and return them for the lifespan.

    A plain route rather than a mount, so ``/mcp`` itself answers: a mount
    would redirect it to ``/mcp/``, and an MCP client posting JSON-RPC is not
    obliged to follow a redirect. Built as a ``Route`` rather than through
    ``add_route``, whose signature admits only request handlers: a ``Route``
    given an ASGI app hands it the raw request, which is what this needs.

    ``raise_app_exceptions=False`` so that an endpoint which fails reaches the
    tool as the 500 the exception handlers wrote — something it can report —
    rather than as an exception thrown back through the transport.
    """
    hosted = HostedMcp(httpx.ASGITransport(app=app, raise_app_exceptions=False))
    app.router.routes.append(Route(MCP_PATH, endpoint=hosted, include_in_schema=False))
    return hosted
