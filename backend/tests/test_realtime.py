"""The board socket: who may hold one, and what comes down it.

The protocol is almost nothing — a ``ready``, then a nudge per committed
change — so most of what is worth asserting is at the edges: the close code a
refused client gets, and the promise that a socket costs no database
connection. That last one has its own test because the failure mode is not a
broken socket but a wedged process.
"""

from __future__ import annotations

from contextlib import AsyncExitStack

import pytest
from httpx import AsyncClient

from tests import ws

BOARD = "/api/v1/projects/ATL/board/ws"


def cookie_of(client: AsyncClient) -> str:
    """The session cookie a signed-in client holds, as a browser would send it."""
    jar = "; ".join(f"{name}={value}" for name, value in client.cookies.items())
    assert jar, "the client is not signed in"
    return jar


async def make_project(client: AsyncClient, key: str = "ATL") -> str:
    """A project with one member, and that member's id to assign cards to."""
    created = await client.post(
        "/projects", json={"key": key, "name": f"{key} project", "description": ""}
    )
    assert created.status_code in (200, 201), created.text
    person = (
        await client.post(
            "/people",
            json={
                "name": f"Owner of {key}",
                "kind": "team",
                "role": "Backend engineer",
                "responsibilities": "Payments.",
            },
        )
    ).json()["id"]
    await client.put(f"/projects/{key}/members", json={"person_ids": [person]})
    return str(person)


async def make_task(client: AsyncClient, assignee: str, key: str = "ATL") -> None:
    response = await client.post(
        f"/projects/{key}/tasks",
        json={
            "title": "Stripe webhook idempotency",
            "description": "Dedupe on event id.",
            "type": "bug",
            "due_date": "2026-09-01",
            "assignee_id": assignee,
        },
    )
    assert response.status_code in (200, 201), response.text


class TestGettingIn:
    async def test_a_signed_in_browser_is_told_it_is_ready(self, signed_in: AsyncClient) -> None:
        await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            assert await socket.receive_json() == {"type": "ready"}

    async def test_no_credential_is_closed_as_unauthorized(self, signed_in: AsyncClient) -> None:
        """4401 and not a handshake failure, because a browser cannot read a
        handshake failure — see `app.realtime.auth`."""
        await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD) as socket:
            assert await socket.expect_close() == 4401

    async def test_the_reason_arrives_before_the_close(self, signed_in: AsyncClient) -> None:
        """The whole point of accepting first: the client can tell "signed
        out" from "server unreachable"."""
        await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD) as socket:
            message = await socket.receive_json()

        assert message["type"] == "error"
        assert message["code"] == "unauthorized"

    async def test_an_unknown_project_is_closed_as_not_found(self, signed_in: AsyncClient) -> None:
        async with ws.connect(
            signed_in.app,
            "/api/v1/projects/NOPE/board/ws",
            cookies=cookie_of(signed_in),
        ) as socket:
            assert await socket.expect_close() == 4404

    async def test_a_foreign_origin_never_gets_a_socket(self, signed_in: AsyncClient) -> None:
        """Refused before accept, so uvicorn answers the handshake with a 403
        and the page opposite never holds an open connection."""
        await make_project(signed_in)

        async with ws.connect(
            signed_in.app,
            BOARD,
            cookies=cookie_of(signed_in),
            headers=[(b"origin", b"https://evil.example")],
        ) as socket:
            assert await socket.expect_close() == 1008
            assert not socket.accepted


class TestBeingTold:
    async def test_a_change_to_the_board_arrives_as_a_nudge(self, signed_in: AsyncClient) -> None:
        person = await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            assert await socket.receive_json() == {"type": "ready"}

            await make_task(signed_in, person)

            nudge = await socket.receive_json()

        assert nudge["type"] == "board.changed"

    async def test_the_nudge_carries_no_board_data(self, signed_in: AsyncClient) -> None:
        """It is a doorbell. Anything more would be a second way to build a
        card, and the second one drifts — see `app.realtime.hub`."""
        person = await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            await socket.receive_json()
            await make_task(signed_in, person)
            nudge = await socket.receive_json()

        assert set(nudge) == {"type", "at"}

    async def test_a_change_to_another_project_is_not_this_board_s_business(
        self, signed_in: AsyncClient
    ) -> None:
        await make_project(signed_in)
        elsewhere = await make_project(signed_in, key="HRM")

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            await socket.receive_json()

            await make_task(signed_in, elsewhere, key="HRM")

            with pytest.raises(TimeoutError):
                await socket.receive_json()

    async def test_nothing_is_said_for_a_change_that_never_happened(
        self, signed_in: AsyncClient
    ) -> None:
        """The outbox lives on the session, so a transaction that did not
        commit discards its news along with its rows."""
        person = await make_project(signed_in)

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            await socket.receive_json()

            refused = await signed_in.post(
                "/projects/ATL/tasks",
                json={
                    "title": "Assigned to nobody who exists",
                    "description": "",
                    "type": "bug",
                    "due_date": "2026-09-01",
                    "assignee_id": "00000000-0000-7000-8000-000000000000",
                },
            )
            assert refused.status_code >= 400, refused.text

            with pytest.raises(TimeoutError):
                await socket.receive_json()

            # And the socket is still good: a refused write is not a broken
            # connection.
            await make_task(signed_in, person)
            assert (await socket.receive_json())["type"] == "board.changed"


class TestWhatASocketCosts:
    async def test_many_open_sockets_leave_the_api_answering(self, signed_in: AsyncClient) -> None:
        """The regression guard for the whole design.

        A handler that held its database session across the receive loop
        would check out one pooled connection per socket. The pool is five
        plus ten overflow, so the fifteenth socket would take the last one and
        every HTTP request after it — including this ``/health``, which uses
        the same dependency — would block for thirty seconds and then raise.
        The symptom is not a broken board; it is a process that appears hung
        and a container that restart-loops on its healthcheck.
        """
        await make_project(signed_in)
        jar = cookie_of(signed_in)

        async with AsyncExitStack() as stack:
            for _ in range(20):
                socket = await stack.enter_async_context(
                    ws.connect(signed_in.app, BOARD, cookies=jar)
                )
                assert await socket.receive_json() == {"type": "ready"}

            health = await signed_in.get("/health")

        assert health.status_code == 200
        assert health.json()["database"] == "up"

    async def test_the_hub_forgets_a_socket_that_went_away(self, signed_in: AsyncClient) -> None:
        await make_project(signed_in)
        hub = signed_in.app.state.hub

        async with ws.connect(signed_in.app, BOARD, cookies=cookie_of(signed_in)) as socket:
            await socket.receive_json()
            assert hub.stats()["board_watchers"] == 1

        # The handler's `finally` has run by the time the context exits.
        assert hub.stats()["board_watchers"] == 0
        assert hub.stats()["projects_watched"] == 0
