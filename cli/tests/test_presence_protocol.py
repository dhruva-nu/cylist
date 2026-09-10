"""The wire formats and the paths, neither of which needs anything running."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from cylist_cli.presence import ipc, protocol

SESSION = "01a08bbf-994b-743b-989c-e07e09e66add"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700, exist_ok=True)
    return tmp_path


class TestTheUrl:
    def test_https_becomes_wss(self) -> None:
        assert protocol.ws_url("https://cylist.example", "s-1") == (
            "wss://cylist.example/api/v1/agent-sessions/s-1/ws"
        )

    def test_http_becomes_ws(self) -> None:
        assert protocol.ws_url("http://localhost:8000", "s-1").startswith("ws://localhost:8000/")

    def test_a_trailing_slash_does_not_double(self) -> None:
        assert "//api" not in protocol.ws_url("https://cylist.example/", "s-1")


class TestReadingAMessage:
    def test_it_reads_what_the_hook_sends(self) -> None:
        parsed = protocol.parse_message(
            protocol.state_message(SESSION, "ATL-1", "working", None, "ATL-1")
        )
        assert parsed is not None
        assert parsed["t"] == "state"
        assert parsed["session"] == SESSION

    @pytest.mark.parametrize(
        "raw",
        [
            "not json",
            "[]",
            '{"t": "state"}',  # no session
            '{"session": "x"}',  # no kind
            '{"t": "nonsense", "session": "x"}',
        ],
    )
    def test_it_refuses_anything_else(self, raw: str) -> None:
        """A daemon that could be killed by a stray byte on its socket would
        be worse than no daemon."""
        assert protocol.parse_message(raw) is None

    def test_it_refuses_an_enormous_line(self) -> None:
        assert protocol.parse_message("x" * (protocol.MAX_LINE + 1)) is None


class TestWhereTheSocketGoes:
    def test_it_is_named_by_a_hash_not_by_the_session(self) -> None:
        """AF_UNIX paths cap near a hundred bytes, and the directory this
        sits in can already be long."""
        path = ipc.socket_path("a" * 300)

        assert len(str(path)) < 100
        assert "aaaa" not in path.name

    def test_the_same_session_always_lands_in_the_same_place(self) -> None:
        assert ipc.socket_path(SESSION) == ipc.socket_path(SESSION)

    def test_two_sessions_do_not_collide(self) -> None:
        assert ipc.socket_path("one") != ipc.socket_path("two")

    def test_the_directory_is_private(self) -> None:
        """It is a channel into a process holding a bearer token."""
        mode = ipc.runtime_dir().stat().st_mode

        assert not mode & (stat.S_IRWXG | stat.S_IRWXO)

    def test_a_loose_directory_is_tightened_rather_than_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        loose = tmp_path / "loose"
        loose.mkdir(mode=0o777)
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(loose))

        resolved = ipc.runtime_dir()

        assert not resolved.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO)

    def test_it_falls_back_when_there_is_no_runtime_dir(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """macOS has no XDG_RUNTIME_DIR, and the fallback is shared, so it
        is checked rather than assumed."""
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)

        resolved = ipc.runtime_dir()

        assert resolved.name == f"cylist-{os.getuid()}"
        assert resolved.stat().st_uid == os.getuid()


class TestWhatTheHookPathCosts:
    def test_websockets_is_never_imported_to_run_a_hook(self) -> None:
        """`commands/__init__.py` imports every command module eagerly.

        A top-level `import websockets` anywhere it can reach would be paid
        by every hook invocation — including the unbound ones, which are
        most of them, make no connection, and are supposed to cost nothing.
        The daemon imports it inside its handler for exactly this reason,
        and that is easy to undo by accident.
        """
        import subprocess
        import sys

        probe = (
            "import sys, cylist_cli.main, cylist_cli.commands;"
            "print([m for m in sys.modules if m.startswith('websockets')])"
        )
        result = subprocess.run(  # noqa: S603 - our own interpreter, our own source
            [sys.executable, "-c", probe], capture_output=True, text=True, check=True
        )

        assert result.stdout.strip() == "[]", result.stdout


class TestSendingWithNobodyThere:
    def test_it_says_so_rather_than_raising(self) -> None:
        """Every way this fails means the same thing to a hook — use HTTP —
        so they are one return value and not three exceptions."""
        assert ipc.send(SESSION, protocol.end_message(SESSION, "session_ended")) is None
