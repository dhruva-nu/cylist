"""An async WebSocket client for the app, because neither stock option works.

``httpx.ASGITransport`` — what :mod:`tests.conftest` builds every HTTP client
on — speaks only http scopes and has no notion of a socket.
``starlette.testclient.TestClient`` does, but it is synchronous: it drives the
app on a worker thread with its own event loop, and calling it from inside a
test that ``asyncio_mode = "auto"`` is already running deadlocks.

So: drive the ASGI callable directly. Two queues and a hand-built scope is
the whole of it, and it buys exact assertions on close codes, which is most
of what there is to test about a socket that carries almost no data.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager, suppress
from typing import Any

from fastapi import FastAPI

RECEIVE_TIMEOUT = 2.0
"""Long enough for a slow machine, short enough that a socket which never
answers fails the test instead of hanging the suite."""


class SocketClosedError(Exception):
    """The app closed the socket. Carries the code the test wants to assert."""

    def __init__(self, code: int) -> None:
        super().__init__(f"socket closed with {code}")
        self.code = code


class WebSocketSession:
    """One connection to the app, from the client's side of the wire."""

    def __init__(self, app: FastAPI, scope: dict[str, Any]) -> None:
        self._app = app
        self._scope = scope
        self._to_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from_app: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None
        self.accepted = False

    async def __aenter__(self) -> WebSocketSession:
        self._task = asyncio.create_task(
            self._app(self._scope, self._to_app.get, self._from_app.put)
        )
        await self._to_app.put({"type": "websocket.connect"})
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        """Hang up and let the handler finish.

        Cancelled rather than awaited indefinitely: a handler blocked on a
        queue that will never fill is the normal shape of a board socket, and
        the test is done with it.
        """
        if self._task is None:
            return
        await self._to_app.put({"type": "websocket.disconnect", "code": 1000})
        self._task.cancel()
        # The handler's own teardown is what matters, and cancelling is how a
        # board socket normally ends — it is parked on a queue that will never
        # fill. Whatever it raised on the way out is the test's business only
        # if the test went looking for it.
        with suppress(BaseException):
            await self._task
        self._task = None

    async def receive(self) -> dict[str, Any]:
        """The next message the app sent, or raise :class:`SocketClosedError`."""
        message = await asyncio.wait_for(self._from_app.get(), RECEIVE_TIMEOUT)
        if message["type"] == "websocket.accept":
            self.accepted = True
            return await self.receive()
        if message["type"] == "websocket.close":
            raise SocketClosedError(message.get("code", 1000))
        return message

    async def receive_json(self) -> Any:
        message = await self.receive()
        return message.get("json") if "json" in message else _loads(message)

    async def expect_close(self) -> int:
        """Drain until the app closes, and return the code.

        Any frames on the way are discarded: a refusal sends its reason
        before it closes, and a test asserting the *code* should not have to
        read the sentence first.
        """
        while True:
            try:
                await self.receive()
            except SocketClosedError as closed:
                return closed.code


def _loads(message: dict[str, Any]) -> Any:
    if (text := message.get("text")) is not None:
        return json.loads(text)
    return json.loads(message["bytes"].decode())


@asynccontextmanager
async def connect(
    app: FastAPI,
    path: str,
    *,
    headers: Iterable[tuple[bytes, bytes]] = (),
    cookies: str | None = None,
) -> AsyncIterator[WebSocketSession]:
    """Open a socket to ``path`` on ``app``.

    ``headers`` and ``cookies`` are how a test authenticates: an agent sets
    ``Authorization``, a browser sets the session cookie, and the handshake
    reads whichever it finds through the same code a request does.
    """
    raw = list(headers)
    raw.append((b"host", b"testserver"))
    if cookies is not None:
        raw.append((b"cookie", cookies.encode()))

    scope: dict[str, Any] = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "server": ("testserver", 80),
        "client": ("testclient", 50000),
        "root_path": "",
        "path": path,
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": raw,
        "subprotocols": [],
        "state": {},
        "app": app,
    }
    session = WebSocketSession(app, scope)
    async with session:
        yield session
