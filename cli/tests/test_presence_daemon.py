"""The daemon's plumbing: threads, the queue, and the socket it holds.

Everything about *when* is tested in `test_presence_machine.py`, against the
pure reducer and without a clock. What is left here is the wiring — that a
line arriving on the unix socket becomes a frame on the WebSocket, that a
dead connection is retried, and that the process lets go of its lock — and
it is driven against a fake connection so there is no network and nothing
to wait for.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest

from cylist_cli.config import Config
from cylist_cli.context import Context
from cylist_cli.presence import daemon, ipc, protocol, supervisor

SESSION = "01a08bbf-994b-743b-989c-e07e09e66add"
CARD = "ATL-1"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700, exist_ok=True)
    return tmp_path


class FakeConnection:
    """A socket that records rather than sends, and can be made to fail."""

    def __init__(self, fail_after: int | None = None) -> None:
        self.sent: list[dict[str, Any]] = []
        self.closed = False
        self._fail_after = fail_after

    def send(self, frame: str) -> None:
        if self._fail_after is not None and len(self.sent) >= self._fail_after:
            raise ConnectionResetError("the peer went away")
        self.sent.append(json.loads(frame))

    def recv(self, timeout: float | None = None) -> str:
        raise TimeoutError

    def close(self) -> None:
        self.closed = True


def a_context() -> Context:
    return Context(
        config=Config(url="http://cylist.test", token="cyl_" + "a" * 43, token_source="env")
    )


def run_in_background(ctx: Context, connect: Any) -> threading.Thread:
    thread = threading.Thread(target=daemon.run, args=(ctx, SESSION), kwargs={"connect": connect})
    thread.start()
    return thread


def tell(line: str) -> dict | None:
    """Say something to the daemon over its real socket, as a hook would."""
    for _ in range(100):
        ack = ipc.send(SESSION, line, timeout=0.2)
        if ack is not None:
            return ack
        threading.Event().wait(0.02)
    return None


class TestTheLoop:
    def test_a_line_on_the_socket_becomes_a_frame_on_the_wire(self) -> None:
        connection = FakeConnection()
        thread = run_in_background(a_context(), lambda *_: connection)
        try:
            assert tell(protocol.bind_message(SESSION, CARD, CARD)) is not None
            assert (
                tell(protocol.state_message(SESSION, CARD, "waiting", "turn_ended", CARD))
                is not None
            )
            threading.Event().wait(0.2)

            states = [f["report"]["state"] for f in connection.sent if f["type"] == "state"]
            assert "waiting" in states
        finally:
            tell(protocol.end_message(SESSION, "session_ended"))
            thread.join(timeout=3)

    def test_it_says_goodbye_and_lets_go(self) -> None:
        connection = FakeConnection()
        thread = run_in_background(a_context(), lambda *_: connection)
        try:
            tell(protocol.bind_message(SESSION, CARD, CARD))
            tell(protocol.end_message(SESSION, "session_ended"))
            thread.join(timeout=3)
        finally:
            if thread.is_alive():  # pragma: no cover - only on a failure
                pytest.fail("the daemon did not stop")

        assert {"type": "bye", "reason": "session_ended"} in connection.sent
        assert connection.closed
        assert supervisor.is_running(SESSION) is False
        assert not ipc.socket_path(SESSION).exists()

    def test_a_broken_connection_is_replaced(self) -> None:
        """The first one dies on its second send; the daemon reconnects and
        tells the new one where the session is, rather than replaying how it
        got there."""
        connections = [FakeConnection(fail_after=1), FakeConnection()]
        handed: list[FakeConnection] = []

        def connect(*_: Any) -> FakeConnection:
            connection = connections[min(len(handed), len(connections) - 1)]
            handed.append(connection)
            return connection

        thread = run_in_background(a_context(), connect)
        try:
            tell(protocol.bind_message(SESSION, CARD, CARD))
            tell(protocol.state_message(SESSION, CARD, "waiting", "turn_ended", CARD))
            for _ in range(100):
                if len(handed) > 1 and connections[1].sent:
                    break
                threading.Event().wait(0.02)

            assert len(handed) > 1, "the daemon did not reconnect"
            assert connections[1].sent[0]["type"] == "state"
        finally:
            tell(protocol.end_message(SESSION, "session_ended"))
            thread.join(timeout=3)

    def test_a_second_daemon_for_the_same_session_exits_quietly(self) -> None:
        """Two hooks racing to start one is the ordinary case, not an error."""
        connection = FakeConnection()
        thread = run_in_background(a_context(), lambda *_: connection)
        try:
            tell(protocol.bind_message(SESSION, CARD, CARD))

            assert daemon.run(a_context(), SESSION, connect=lambda *_: FakeConnection()) == 0
            # And the first one still owns the socket.
            assert tell(protocol.state_message(SESSION, CARD, "working", None, CARD)) is not None
        finally:
            tell(protocol.end_message(SESSION, "session_ended"))
            thread.join(timeout=3)

    def test_a_message_for_another_session_is_refused(self) -> None:
        """Cheap, and the alternative is one conversation's state landing on
        another's card."""
        connection = FakeConnection()
        thread = run_in_background(a_context(), lambda *_: connection)
        try:
            tell(protocol.bind_message(SESSION, CARD, CARD))

            intruder = protocol.state_message("someone-else", CARD, "working", None, CARD)
            assert ipc.send(SESSION, intruder, timeout=0.5) is None
        finally:
            tell(protocol.end_message(SESSION, "session_ended"))
            thread.join(timeout=3)


class TestWithoutAToken:
    def test_it_does_not_start(self) -> None:
        """Before `cylist login` there is nothing to report with, and a
        daemon that sat there retrying would be a process nobody asked for."""
        ctx = Context(config=Config(url="http://cylist.test", token=None, token_source="none"))

        assert daemon.run(ctx, SESSION, connect=lambda *_: FakeConnection()) == 0
        assert supervisor.is_running(SESSION) is False
