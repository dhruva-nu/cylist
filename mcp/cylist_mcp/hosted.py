"""The same tools, served over HTTP by the Cylist server itself.

Run on a laptop, this package is a stdio process that Claude Code starts, and
setting that up means uv, Python, a token on disk and a ``claude mcp add`` —
which is what ``cylist setup`` exists to do. Served from the backend, it is a
URL. Connecting a machine is then one ``claude mcp add --transport http`` line
with the token in a header, and the machine needs nothing but the MCP client:
no install, no config file, and no list of addresses to fail over between,
because the only address is the one the line names.

**Every request is the caller's own.** The token in its ``Authorization``
header is checked with ``GET /me`` before the request reaches a tool, and the
tools then call the API *with that token* — the same client the stdio server
uses, pointed at whatever transport the host hands in (in the backend, the app
itself, in-process). So a hosted tool can do exactly what that token can do
over plain HTTP and nothing more: the API's own scope checks stay the only
ones, and the activity feed names whoever minted the token, as it does now.

**``reveal_secret`` still exists only for a token that can use it.** One
process serves every token here, so the choice is made per request rather
than at startup: there are two servers, one with the tool and one without,
and each request goes to the one its token's scopes select.

**Stateless.** Each POST is answered on its own and nothing is kept between
them, so there are no sessions to leak, expire or lose to a restart — which
matters because the backend is one process that restarts on every deploy.

A bearer header is the only credential read. A session cookie is ignored, so
a page in someone's browser cannot drive these tools on the strength of their
being signed in to the board; and that is also why the SDK's DNS-rebinding
check (a Host allow-list, meant for servers on localhost with no auth at all)
is switched off rather than configured.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from contextvars import ContextVar
from typing import Any

import httpx
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.transport_security import TransportSecuritySettings
from starlette.types import Receive, Scope, Send

from cylist_mcp.client import ApiClient
from cylist_mcp.errors import CylistError
from cylist_mcp.server import VAULT_REVEAL, build_server

_caller: ContextVar[ApiClient] = ContextVar("cylist_mcp_caller")
"""The client carrying the token of the request being served."""


class _Caller:
    """An :class:`~cylist_mcp.client.Api` that is whoever is asking right now.

    The tools are built once, at startup, around one client object. Here that
    object is this: each call looks up the client the current request brought.
    A context variable rather than an argument, because it has to reach the
    tool body through the SDK's dispatch without the tools knowing about it —
    they are the same functions the stdio server runs.
    """

    async def get(self, path: str, **params: Any) -> Any:
        return await _caller.get().get(path, **params)

    async def get_text(self, path: str, *, max_chars: int) -> tuple[str, bool]:
        return await _caller.get().get_text(path, max_chars=max_chars)

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await _caller.get().post(path, body)

    async def patch(self, path: str, body: dict[str, Any]) -> Any:
        return await _caller.get().patch(path, body)


class HostedMcp:
    """An ASGI app answering MCP's streamable HTTP transport.

    ``transport`` is how the tools reach the API, and ``base_url`` is the
    address they use through it. The backend passes an
    ``httpx.ASGITransport`` over its own app, so a tool call never leaves the
    process; a test passes a mock.

    :meth:`run` has to be entered before the first request — it is what starts
    the SDK's session managers — so the host puts it in its lifespan.
    """

    def __init__(
        self,
        transport: httpx.AsyncBaseTransport,
        *,
        base_url: str = "http://cylist.internal",
    ) -> None:
        self._transport = transport
        self._base_url = base_url
        caller = _Caller()
        self._managers = {
            False: _manager(caller, frozenset()),
            True: _manager(caller, frozenset({VAULT_REVEAL})),
        }

    @asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        async with AsyncExitStack() as stack:
            for manager in self._managers.values():
                await stack.enter_async_context(manager.run())
            yield

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return

        if scope["method"] != "POST":
            # GET would open the stream a server pushes notifications down,
            # and DELETE would end a session. Stateless, there is nothing to
            # push and no session to end — and the SDK holds a GET open
            # regardless, an idle socket per stray request. The spec's answer
            # for a server without that stream is 405.
            await _refuse(send, 405, "method_not_allowed", "This endpoint takes POST only.")
            return

        token = _bearer(scope)
        if token is None:
            await _refuse(
                send,
                401,
                "unauthenticated",
                "Send the token as 'Authorization: Bearer cyl_...'. Mint one from "
                "the Agents page of any project, which also shows the line that "
                "connects Claude Code.",
            )
            return

        client = ApiClient(self._base_url, token, transport=self._transport)
        try:
            try:
                identity = await client.get("/me")
            except CylistError as exc:
                status = 401 if exc.status_code == 401 else 502
                await _refuse(send, status, exc.code, exc.message)
                return

            scopes = frozenset(str(scope) for scope in identity.get("scopes", []))
            bound = _caller.set(client)
            try:
                manager = self._managers[VAULT_REVEAL in scopes]
                await manager.handle_request(scope, receive, send)
            finally:
                _caller.reset(bound)
        finally:
            await client.aclose()


def _manager(caller: _Caller, scopes: frozenset[str]) -> StreamableHTTPSessionManager:
    server = build_server(caller, scopes, hosted=True)
    # Built for its side effect: the SDK creates the session manager here,
    # and ``session_manager`` is the public way to reach it. The Starlette
    # app it returns is not used — this class is the app.
    server.streamable_http_app(
        stateless_http=True,
        json_response=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )
    return server.session_manager


def _bearer(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == b"authorization":
            kind, _, token = bytes(value).decode("latin-1").partition(" ")
            if kind.lower() == "bearer" and token.strip():
                return token.strip()
    return None


async def _refuse(send: Send, status: int, code: str, message: str) -> None:
    """Answer in the API's own error envelope, before MCP is involved at all."""
    body = json.dumps({"error": {"code": code, "message": message}}).encode()
    headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode()),
    ]
    if status == 401:
        headers.append((b"www-authenticate", b"Bearer"))
    if status == 405:
        headers.append((b"allow", b"POST"))
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})
