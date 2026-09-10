"""One test that uses a real socket, because everything else does not.

The rest of the realtime suite drives the ASGI callable directly, which is
fast and exact and proves nothing whatsoever about the HTTP upgrade — the
handshake, the `Authorization` header on it, the framing, the close
handshake. Those are all uvicorn's and `websockets`'s work rather than ours,
right up until the moment they do not happen, and then the symptom is a
board that silently never connects.

So: a real uvicorn on a real port, and a real client. It is the only test
here with genuine flake risk, which is why there is exactly one of it and
why everything it can delegate to the faster tests, it does.

Deliberately *not* through Tailscale, which is the other half of the same
question and the one this cannot answer — see DEPLOY.md, which carries a
probe to run against a deployment for that.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import threading
from collections.abc import Iterator

import pytest
import uvicorn
from httpx import AsyncClient

from app.config import Settings
from app.db import Database
from app.main import create_app
from tests.test_agent_reports import SESSION_A, a_card, states


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


@pytest.fixture
def served(settings: Settings, database: Database) -> Iterator[str]:
    """A real uvicorn serving the app on loopback, for the life of one test."""
    app = create_app(settings)
    app.state.settings = settings
    app.state.database = database
    database.publish_to(app.state.hub.publish)

    port = free_port()
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            # Leave the process's logging alone. uvicorn's default config
            # calls `dictConfig` and takes over the root handlers, which in
            # a test run means pytest's capture is replaced underneath every
            # later test and anything logging at teardown fails on a closed
            # stream. The app configures its own logging in `create_app`.
            log_config=None,
            log_level="warning",
            # The same numbers the production image passes. Testing the
            # defaults would be testing something we do not ship.
            ws_ping_interval=20,
            ws_ping_timeout=20,
            ws_max_size=65536,
        )
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            threading.Event().wait(0.05)
        assert server.started, "uvicorn did not come up"
        yield f"127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=5)
        database.publish_to(None)


async def an_agent_token(signed_in: AsyncClient) -> str:
    issued = await signed_in.post(
        "/tokens", json={"name": "claude code hook", "scopes": ["read", "write"]}
    )
    assert issued.status_code in (200, 201), issued.text
    return str(issued.json()["token"])


class TestARealUpgrade:
    async def test_an_agent_reports_over_a_real_socket_and_the_close_ends_it(
        self, signed_in: AsyncClient, served: str
    ) -> None:
        """The whole path, end to end, with nothing faked.

        A real HTTP upgrade carrying a bearer header, a real frame, a real
        close — and the row ending because the connection did, which is the
        one thing the PUT it replaced could never manage.
        """
        from websockets.sync.client import connect

        ref = await a_card(signed_in)
        token = await an_agent_token(signed_in)
        url = f"ws://{served}/api/v1/agent-sessions/{SESSION_A}/ws"

        def talk() -> list[dict]:
            seen = []
            with connect(
                url,
                additional_headers={"Authorization": f"Bearer {token}"},
                open_timeout=10,
            ) as socket_:
                seen.append(json.loads(socket_.recv()))
                socket_.send(
                    json.dumps(
                        {
                            "type": "state",
                            "task": ref,
                            "report": {"state": "working", "client_name": ref},
                        }
                    )
                )
                seen.append(json.loads(socket_.recv()))
            return seen

        # The client is synchronous — it is the same one the CLI daemon uses
        # — so it runs off the loop rather than blocking the server's.
        seen = await asyncio.to_thread(talk)

        assert seen[0]["type"] == "ready"
        assert seen[1]["type"] == "ack"
        assert seen[1]["session"]["state"] == "working"

        # The close was clean at the protocol level but said no goodbye, so
        # the session is over and the board knows why.
        for _ in range(100):
            if (await states(signed_in, ref)) == [("done", "connection_lost")]:
                break
            await asyncio.sleep(0.05)

        assert await states(signed_in, ref) == [("done", "connection_lost")]

    async def test_a_bad_token_is_refused_over_a_real_socket_too(
        self, signed_in: AsyncClient, served: str
    ) -> None:
        """Accept-then-close, which is what lets a browser tell "signed out"
        from "server down" — and is invisible unless a real handshake runs."""
        from websockets.exceptions import ConnectionClosed
        from websockets.sync.client import connect

        await a_card(signed_in)
        url = f"ws://{served}/api/v1/agent-sessions/{SESSION_A}/ws"

        def talk() -> int:
            with connect(
                url,
                additional_headers={"Authorization": "Bearer cyl_" + "z" * 43},
                open_timeout=10,
            ) as socket_:
                with contextlib.suppress(ConnectionClosed):
                    while True:
                        socket_.recv()
                return int(socket_.close_code or 0)

        assert await asyncio.to_thread(talk) == 4401
