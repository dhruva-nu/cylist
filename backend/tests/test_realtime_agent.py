"""The agent socket: reporting over a connection instead of a series of them.

The messages matter less than the close. A socket that ends for any reason
ends the row with it, which is the whole difference from the PUT it replaces
— and the reason a board can stop guessing from a clock.
"""

from __future__ import annotations

import asyncio

import pytest
from httpx import AsyncClient

from app.realtime import auth
from app.services import agent_feed
from tests import ws
from tests.test_agent_reports import SESSION_A, a_card, states

SOCKET = f"/api/v1/agent-sessions/{SESSION_A}/ws"


@pytest.fixture
async def token(signed_in: AsyncClient) -> str:
    issued = await signed_in.post(
        "/tokens", json={"name": "claude code hook", "scopes": ["read", "write"]}
    )
    assert issued.status_code in (200, 201), issued.text
    return str(issued.json()["token"])


def bearer(token: str) -> list[tuple[bytes, bytes]]:
    return [(b"authorization", f"Bearer {token}".encode())]


def working(ref: str) -> dict[str, object]:
    return {
        "type": "state",
        "task": ref,
        "report": {"state": "working", "client_name": ref},
    }


class TestGettingIn:
    async def test_an_agent_token_is_told_it_is_ready(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            hello = await socket.receive_json()

        assert hello["type"] == "ready"
        assert hello["actor_label"] == "claude code hook"

    async def test_a_browser_session_is_refused(self, signed_in: AsyncClient) -> None:
        """The rule `upsert` has always enforced, moved to the handshake: a
        person is not an agent whatever they type."""
        await a_card(signed_in)
        jar = "; ".join(f"{k}={v}" for k, v in signed_in.cookies.items())

        async with ws.connect(signed_in.app, SOCKET, cookies=jar) as socket:
            assert await socket.expect_close() == auth.FORBIDDEN

    async def test_a_token_without_write_is_refused(self, signed_in: AsyncClient) -> None:
        issued = await signed_in.post("/tokens", json={"name": "reader", "scopes": ["read"]})
        reader = issued.json()["token"]

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(reader)) as socket:
            assert await socket.expect_close() == auth.FORBIDDEN


class TestReporting:
    async def test_a_state_message_puts_the_agent_on_the_card(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            acked = await socket.receive_json()

            assert acked["type"] == "ack"
            assert acked["session"]["state"] == "working"
            assert await states(signed_in, ref) == [("working", None)]

    async def test_an_unknown_card_is_refused_without_killing_the_row(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json({**working("ATL-999"), "task": "ATL-999"})

            assert await socket.expect_close() == auth.NOT_FOUND

    async def test_a_frame_it_cannot_read_closes_the_socket(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_text("{not json")

            assert await socket.expect_close() == auth.BAD_FRAME

    async def test_a_heartbeat_writes_nothing_and_says_nothing(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        """It exists to move the idle deadline. Anything else it did would be
        a write per minute, which is the thing this ticket removed."""
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()

            await socket.send_json({"type": "heartbeat"})

            with pytest.raises(TimeoutError):
                await socket.receive_json()
            assert await states(signed_in, ref) == [("working", None)]


class TestEnding:
    async def test_a_goodbye_ends_the_session_as_ended(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()

            await socket.send_json({"type": "bye"})
            assert await socket.expect_close() == 1000

        assert await states(signed_in, ref) == [("done", "session_ended")]

    async def test_a_dropped_socket_ends_the_session_as_lost(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        """The whole point. Nothing said goodbye, and the card still stops
        claiming an agent is on it."""
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()
            assert await states(signed_in, ref) == [("working", None)]

        # Leaving the block hangs up without a `bye`.
        assert await states(signed_in, ref) == [("done", "connection_lost")]

    async def test_going_quiet_past_the_window_closes_it(
        self, signed_in: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import timedelta

        from app.realtime import routes

        monkeypatch.setattr(routes, "AGENT_SOCKET_IDLE_AFTER", timedelta(milliseconds=150))
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()

            assert await socket.expect_close() == auth.IDLE

        assert await states(signed_in, ref) == [("done", "connection_lost")]

    async def test_the_trail_says_which_kind_of_loss_it_was(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()

        history = (await signed_in.get(f"/tasks/{ref}/history")).json()
        finished = [e for e in history["entries"] if e["verb"] == "agent_session.finished"]
        assert finished, history
        # One reason on the card, the detail underneath it.
        assert finished[0]["payload"]["reason"] == "connection_lost"
        assert finished[0]["payload"]["cause"] == "dropped"


class TestReconnecting:
    async def test_the_newer_socket_takes_the_session(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        """A reconnect after a blip must not have its own row ended by the
        corpse of the connection it replaced — the stale socket may be a
        half-dead TCP connection that ping/pong will not give up on for
        another forty seconds."""
        ref = await a_card(signed_in)
        hub = signed_in.app.state.hub

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as first:
            await first.receive_json()
            await first.send_json(working(ref))
            await first.receive_json()

            async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as second:
                await second.receive_json()
                await second.send_json(working(ref))
                await second.receive_json()

                assert hub.stats()["agent_sockets"] == 1
                # The displaced socket is torn down without touching the row.
                await first.close()
                await asyncio.sleep(0.05)

                assert await states(signed_in, ref) == [("working", None)]


def feed(ref: str, *entries: dict[str, object]) -> dict[str, object]:
    return {"type": "feed", "task": ref, "entries": list(entries)}


def line(text: str, kind: str = "run", tool: str | None = "Bash") -> dict[str, object]:
    return {"at": "2026-10-12T14:31:02Z", "kind": kind, "tool": tool, "text": text}


class TestTheFeed:
    """CYLIST-88: what the agent read, edited, ran and said, kept per session."""

    async def test_lines_are_kept_and_read_back_newest_first(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()

            await socket.send_json(
                feed(
                    ref,
                    line("Starting with the model.", kind="said", tool=None),
                    line("backend/app/models/task.py", kind="read", tool="Read"),
                    line("pytest test_pr_states.py"),
                )
            )
            # Unacknowledged: a feed line is not worth a round trip.
            with pytest.raises(TimeoutError):
                await socket.receive_json()

        lines = (await signed_in.get(f"/tasks/{ref}/agent-feed")).json()
        assert [(entry["kind"], entry["text"]) for entry in lines] == [
            ("run", "pytest test_pr_states.py"),
            ("read", "backend/app/models/task.py"),
            ("said", "Starting with the model."),
        ]
        assert lines[0]["tool"] == "Bash"
        assert lines[0]["client_name"] == ref

    async def test_a_feed_is_not_history(self, signed_in: AsyncClient, token: str) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()
            await socket.send_json(feed(ref, line("ls"), line("git status")))
            await asyncio.sleep(0.05)

        history = (await signed_in.get(f"/tasks/{ref}/history")).json()["entries"]
        assert not [entry for entry in history if "feed" in str(entry)]

    async def test_lines_for_a_card_the_session_is_not_on_are_dropped(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        """The daemon follows its transcript whatever it has reported, so a
        frame can outrun the state that puts the session on the card. Dropped,
        and the socket carries on: a feed is never a reason to leave the board."""
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(feed(ref, line("too early")))
            await socket.send_json(feed("ATL-999", line("nowhere")))

            await socket.send_json(working(ref))
            assert (await socket.receive_json())["type"] == "ack"

        assert (await signed_in.get(f"/tasks/{ref}/agent-feed")).json() == []

    async def test_a_line_of_an_unknown_kind_is_not_a_frame(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(feed(ref, line("x", kind="thought")))

            assert await socket.expect_close() == auth.BAD_FRAME

    async def test_each_session_keeps_only_its_newest_lines(
        self, signed_in: AsyncClient, token: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(agent_feed, "KEEP_PER_SESSION", 3)
        ref = await a_card(signed_in)

        async with ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket:
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()
            await socket.send_json(feed(ref, *(line(f"step {n}") for n in range(5))))
            await asyncio.sleep(0.05)

        lines = (await signed_in.get(f"/tasks/{ref}/agent-feed")).json()
        assert [entry["text"] for entry in lines] == ["step 4", "step 3", "step 2"]

    async def test_a_board_watching_the_card_hears_of_it_by_card(
        self, signed_in: AsyncClient, token: str
    ) -> None:
        """A doorbell of its own, naming the card: the board does not refetch
        every card because one agent ran `ls`."""
        ref = await a_card(signed_in)
        task_id = (await signed_in.get(f"/tasks/{ref}")).json()["id"]
        jar = "; ".join(f"{k}={v}" for k, v in signed_in.cookies.items())

        async with (
            ws.connect(signed_in.app, "/api/v1/projects/ATL/board/ws", cookies=jar) as board,
            ws.connect(signed_in.app, SOCKET, headers=bearer(token)) as socket,
        ):
            await board.receive_json()
            await socket.receive_json()
            await socket.send_json(working(ref))
            await socket.receive_json()
            assert (await board.receive_json())["type"] == "board.changed"

            await socket.send_json(feed(ref, line("pytest")))

            nudge = await board.receive_json()
            assert nudge["type"] == "agent.feed"
            assert nudge["task_id"] == task_id
            assert set(nudge) == {"type", "task_id", "at"}

    async def test_reading_the_feed_needs_only_read(self, signed_in: AsyncClient) -> None:
        ref = await a_card(signed_in)
        issued = await signed_in.post("/tokens", json={"name": "reader", "scopes": ["read"]})
        headers = {"Authorization": f"Bearer {issued.json()['token']}"}

        response = await signed_in.get(f"/tasks/{ref}/agent-feed", headers=headers)

        assert response.status_code == 200
        assert response.json() == []
