"""The hook talking to a daemon, and what it does when it cannot.

The rule under all of it: a hook must never make a prompt worse. Whatever is
wrong with the socket — nothing listening, a wedged process, a reply from
another session — the answer is the same, and it is the answer Cylist gave
before any of this existed: report over HTTP and get out of the way.
"""

from __future__ import annotations

import io
import json
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from cylist_cli.main import main
from cylist_cli.presence import supervisor
from tests import fake_api, fake_daemon

SESSION = "01a08bbf-994b-743b-989c-e07e09e66add"
CARD = "ATL-1"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700, exist_ok=True)
    monkeypatch.delenv("CYLIST_TASK", raising=False)
    monkeypatch.setenv("CYLIST_PRESENCE", "ws")
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_" + "a" * 43)
    monkeypatch.setenv("CYLIST_URL", "http://cylist.test")
    return tmp_path


@pytest.fixture(autouse=True)
def no_real_processes(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Record what would have been spawned, and never actually spawn it.

    Autouse and unconditional: a test suite that can leave a background
    process behind on a developer's machine is one nobody should have to
    think about while running.
    """
    spawned: list[dict[str, Any]] = []

    def record(argv: list[str], **kwargs: Any) -> Any:
        spawned.append({"argv": argv, **kwargs})
        return None

    monkeypatch.setattr(subprocess, "Popen", record)
    return spawned


Fire = Callable[..., tuple[int, str, str]]


@pytest.fixture
def recorder() -> fake_api.Recorder:
    return fake_api.Recorder()


@pytest.fixture
def fire(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    recorder: fake_api.Recorder,
) -> Fire:
    def invoke(event: dict[str, Any]) -> tuple[int, str, str]:
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
        code = main(["hook"], transport=fake_api.build(recorder))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


def event(name: str, **fields: Any) -> dict[str, Any]:
    return {
        "session_id": SESSION,
        "cwd": "/home/somebody/project",
        "hook_event_name": name,
        **fields,
    }


def bind(task: str = CARD) -> None:
    from cylist_cli.commands import hook

    hook._save(SESSION, {"task": task, "title_applied": task})


class TestWhenThereIsADaemon:
    def test_the_report_goes_down_the_socket_and_not_over_http(
        self, fire: Fire, recorder: fake_api.Recorder
    ) -> None:
        bind()
        with fake_daemon.FakeDaemon(SESSION) as daemon:
            fire(event("Stop"))

        assert recorder.paths() == []
        assert daemon.received[0]["t"] == "state"
        assert daemon.received[0]["state"] == "waiting"
        assert daemon.received[0]["reason"] == "turn_ended"

    def test_walking_to_another_card_is_a_bind(
        self, fire: Fire, recorder: fake_api.Recorder
    ) -> None:
        bind("ATL-1")
        with fake_daemon.FakeDaemon(SESSION) as daemon:
            fire(
                event(
                    "PostToolUse",
                    tool_name="mcp__cylist_prod__move_task",
                    tool_input={"task": "ATL-2"},
                )
            )

        assert daemon.received[0]["t"] == "bind"
        assert daemon.received[0]["task"] == "ATL-2"
        assert recorder.paths() == []

    def test_the_end_of_a_session_is_told_to_the_daemon(
        self, fire: Fire, recorder: fake_api.Recorder
    ) -> None:
        bind()
        with fake_daemon.FakeDaemon(SESSION) as daemon:
            fire(event("SessionEnd", reason="exit"))

        assert daemon.received[0]["t"] == "end"
        assert recorder.paths() == []

    def test_no_daemon_is_started_when_one_is_answering(
        self, fire: Fire, no_real_processes: list[dict[str, Any]]
    ) -> None:
        bind()
        with fake_daemon.FakeDaemon(SESSION):
            fire(event("Stop"))

        assert no_real_processes == []


class TestWhenThereIsNot:
    def test_it_reports_over_http_and_starts_one_for_next_time(
        self, fire: Fire, recorder: fake_api.Recorder, no_real_processes: list[dict[str, Any]]
    ) -> None:
        """This event still has to arrive. A fresh daemon has a server to
        reach before it is any use, and a prompt must not wait for that."""
        bind()

        fire(event("Stop"))

        assert recorder.count("PUT", "/agent-sessions/" + SESSION) == 1
        assert len(no_real_processes) == 1
        assert no_real_processes[0]["argv"][-3:] == ["daemon", "--session", SESSION]

    def test_the_child_never_inherits_the_hook_s_stdout(
        self, fire: Fire, no_real_processes: list[dict[str, Any]]
    ) -> None:
        """Claude Code reads a hook's stdout until EOF.

        A detached child holding that pipe open would make *every* hook
        invocation appear to hang until its three-second timeout — a symptom
        about as far from its cause as it is possible to get. This is a
        one-line mistake to make and a very slow one to find.
        """
        bind()

        fire(event("Stop"))

        spawned = no_real_processes[0]
        assert spawned["stdout"] is subprocess.DEVNULL
        assert spawned["stderr"] is subprocess.DEVNULL
        assert spawned["stdin"] is subprocess.DEVNULL
        assert spawned["start_new_session"] is True

    def test_an_unbound_session_starts_nothing_and_says_nothing(
        self, fire: Fire, recorder: fake_api.Recorder, no_real_processes: list[dict[str, Any]]
    ) -> None:
        """The rule that has to survive everything: the hooks are installed
        user-wide and fire in every project."""
        fire(event("Stop"))

        assert recorder.paths() == []
        assert no_real_processes == []

    def test_ending_a_session_never_starts_a_daemon(
        self, fire: Fire, recorder: fake_api.Recorder, no_real_processes: list[dict[str, Any]]
    ) -> None:
        """Spawning here would leave a process behind to time itself out."""
        bind()

        fire(event("SessionEnd", reason="exit"))

        assert no_real_processes == []
        assert recorder.count("PUT", "/agent-sessions/" + SESSION) == 1


class TestWhenTheDaemonIsWrong:
    @pytest.mark.parametrize("behaviour", ["hang", "wrong_session", "garbage"])
    def test_it_falls_back_to_http(
        self, behaviour: str, fire: Fire, recorder: fake_api.Recorder
    ) -> None:
        """Three different failures, one answer. A hook is not the place to
        be clever about which kind of broken it is looking at."""
        bind()
        with fake_daemon.FakeDaemon(SESSION, behaviour=behaviour):
            code, _, _ = fire(event("Stop"))

        assert code == 0
        assert recorder.count("PUT", "/agent-sessions/" + SESSION) == 1

    def test_a_wedged_daemon_does_not_hold_the_prompt(
        self, fire: Fire, recorder: fake_api.Recorder
    ) -> None:
        import time

        bind()
        with fake_daemon.FakeDaemon(SESSION, behaviour="hang"):
            started = time.monotonic()
            fire(event("Stop"))
            took = time.monotonic() - started

        # The socket's own quarter-second, plus the HTTP fallback. Well
        # inside the three seconds Claude Code allows a hook.
        assert took < 2.0


class TestTheSwitch:
    def test_off_uses_neither_channel_beyond_http(
        self,
        fire: Fire,
        recorder: fake_api.Recorder,
        no_real_processes: list[dict[str, Any]],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """For somebody who does not want a background process. It reports
        the way it always did rather than not reporting at all."""
        monkeypatch.setenv("CYLIST_PRESENCE", "off")
        bind()

        fire(event("Stop"))

        assert no_real_processes == []
        assert recorder.count("PUT", "/agent-sessions/" + SESSION) == 1


class TestTheSingleton:
    def test_a_lock_is_only_held_by_one(self, isolated: Path) -> None:
        first = supervisor.Singleton(SESSION)
        assert first.acquire() is True
        assert supervisor.is_running(SESSION) is True

        second = supervisor.Singleton(SESSION)
        assert second.acquire() is False

        first.release()
        assert supervisor.is_running(SESSION) is False

    def test_a_lock_left_by_a_dead_process_holds_nothing(self, isolated: Path) -> None:
        """Why it is an flock and not a pidfile: the kernel drops it however
        the process died, so there is no stale state to clean up and no pid
        to be reused underneath us."""
        held = supervisor.Singleton(SESSION)
        held.acquire()
        held.release()

        assert supervisor.is_running(SESSION) is False
