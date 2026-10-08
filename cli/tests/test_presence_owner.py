"""Finding the ``claude`` a session belongs to, and noticing when it goes.

Driven against real processes, not a mock of the process table — a child
this suite starts and then kills *is* the case the daemon has to get right,
and it costs milliseconds. The ancestry walk is the one part given a fake
table, because arranging a real grandparent called ``claude`` would mean
copying an executable to a temporary name and is a test of ``shutil`` by the
time it works.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any

import pytest

from cylist_cli.presence import owner
from tests.conftest import needs_a_process_table

IDLE_CHILD = "import time; time.sleep(60)"


@pytest.fixture
def child() -> Any:
    """A process this test owns, and can end whenever it likes."""
    started = subprocess.Popen([sys.executable, "-c", IDLE_CHILD])  # noqa: S603
    try:
        yield started
    finally:
        if started.poll() is None:
            started.kill()
        started.wait(timeout=5)


def table(*rows: owner.Process) -> Any:
    """A fake process table, keyed by pid, for the ancestry walk."""
    by_pid = {row.pid: row for row in rows}
    return lambda pid: by_pid.get(pid)


@needs_a_process_table
class TestReadingAProcess:
    def test_it_reads_this_one(self) -> None:
        me = owner.read(os.getpid())

        assert me is not None
        assert me.pid == os.getpid()
        assert me.ppid == os.getppid()
        assert me.started

    def test_a_pid_that_is_not_there_is_not_a_process(self) -> None:
        assert owner.read(0) is None

    def test_the_same_process_reads_the_same_start_every_time(self) -> None:
        """The whole point of the field: it is an identity, not a reading."""
        first = owner.read(os.getpid())
        time.sleep(0.01)
        second = owner.read(os.getpid())

        assert first is not None and second is not None
        assert first.started == second.started


class TestFindingTheOwner:
    def test_it_walks_past_the_shell_to_the_harness(self, monkeypatch: Any) -> None:
        """What a hook actually sees: `claude` ran a shell, the shell ran us.

        Matching on the nearest process would find the shell, which exits the
        moment the hook does — so every card would clear a second after it
        was lit.
        """
        monkeypatch.delenv("CLAUDE_PID", raising=False)
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=30, ppid=20, name="cylist", started="c"),
                owner.Process(pid=20, ppid=10, name="bash", started="b"),
                owner.Process(pid=10, ppid=2, name="claude", started="a"),
            ),
        )

        assert owner.find(30) == owner.Owner(pid=10, started="a")

    def test_a_session_not_started_by_claude_has_no_owner(self, monkeypatch: Any) -> None:
        """And therefore keeps the five-minute window. Reported as absence
        rather than as a guess, because a wrong owner clears a live card."""
        monkeypatch.delenv("CLAUDE_PID", raising=False)
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=30, ppid=20, name="cylist", started="c"),
                owner.Process(pid=20, ppid=1, name="bash", started="b"),
            ),
        )

        assert owner.find(30) is None

    def test_a_node_install_is_recognised_by_claude_pid(self, monkeypatch: Any) -> None:
        """An npm install runs as `node`, so the name says nothing. The
        harness names itself in the environment, and that is believed — but
        only for a process actually in this hook's ancestry."""
        monkeypatch.setenv("CLAUDE_PID", "10")
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=30, ppid=20, name="cylist", started="c"),
                owner.Process(pid=20, ppid=10, name="bash", started="b"),
                owner.Process(pid=10, ppid=2, name="node", started="a"),
            ),
        )

        assert owner.find(30) == owner.Owner(pid=10, started="a")

    def test_an_inherited_claude_pid_from_outside_is_ignored(self, monkeypatch: Any) -> None:
        """A nested session inherits the outer session's variable. Believing
        it would pin this card to a process that outlives the conversation —
        exactly the bug this module exists to stop, in the other direction."""
        monkeypatch.setenv("CLAUDE_PID", "999")
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=30, ppid=20, name="cylist", started="c"),
                owner.Process(pid=20, ppid=1, name="bash", started="b"),
            ),
        )

        assert owner.find(30) is None

    def test_the_nearest_claude_wins(self, monkeypatch: Any) -> None:
        """A session inside a session belongs to the inner one."""
        monkeypatch.delenv("CLAUDE_PID", raising=False)
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=40, ppid=30, name="cylist", started="d"),
                owner.Process(pid=30, ppid=20, name="claude", started="c"),
                owner.Process(pid=20, ppid=10, name="bash", started="b"),
                owner.Process(pid=10, ppid=2, name="claude", started="a"),
            ),
        )

        assert owner.find(40) == owner.Owner(pid=30, started="c")

    def test_a_cycle_in_the_table_does_not_hang(self, monkeypatch: Any) -> None:
        """It cannot happen, and a daemon that spun here would be a machine
        with no board and a hot CPU, so it is cheaper to be sure."""
        monkeypatch.delenv("CLAUDE_PID", raising=False)
        monkeypatch.setattr(
            owner,
            "read",
            table(
                owner.Process(pid=30, ppid=20, name="cylist", started="c"),
                owner.Process(pid=20, ppid=30, name="bash", started="b"),
            ),
        )

        assert owner.find(30) is None

    def test_it_gives_up_rather_than_walking_to_init(self, monkeypatch: Any) -> None:
        depth = owner.MAX_DEPTH + 5
        chain = [
            owner.Process(pid=pid, ppid=pid - 1, name="bash", started=str(pid))
            for pid in range(2, depth + 2)
        ]
        monkeypatch.delenv("CLAUDE_PID", raising=False)
        monkeypatch.setattr(owner, "read", table(*chain, owner.Process(1, 0, "claude", "x")))

        assert owner.find(depth + 1) is None

    @needs_a_process_table
    def test_this_process_is_walked_by_default(self, monkeypatch: Any) -> None:
        """No argument means "whoever is running me", which is what the hook
        wants and is the only call site in the CLI."""
        monkeypatch.setenv("CLAUDE_PID", str(os.getpid()))

        me = owner.read(os.getpid())
        assert me is not None

        assert owner.find() == owner.Owner(pid=os.getpid(), started=me.started)


@needs_a_process_table
class TestWatchingTheOwner:
    def test_a_running_process_is_not_gone(self, child: Any) -> None:
        found = owner.read(child.pid)
        assert found is not None

        watch = owner.Watch(owner.Owner(pid=child.pid, started=found.started))

        assert watch.gone(0.0) is False

    def test_a_dead_process_is_gone(self, child: Any) -> None:
        """Closing the terminal clears the card. The daemon asks this once a
        second, so "within a few seconds" is a tick plus the goodbye."""
        found = owner.read(child.pid)
        assert found is not None
        watch = owner.Watch(owner.Owner(pid=child.pid, started=found.started))
        child.kill()
        child.wait(timeout=5)

        assert watch.gone(0.0) is True

    def test_a_pid_handed_to_somebody_else_is_gone(self, child: Any) -> None:
        """The reason an owner is a pid *and* a start time.

        Simulated by claiming a start time the live process does not have,
        which is indistinguishable from the real thing: a recycled pid is a
        live process that did not start when ours did.
        """
        watch = owner.Watch(owner.Owner(pid=child.pid, started="not-when-it-started"))

        assert watch.gone(owner.RECHECK_EVERY) is True

    def test_identity_is_confirmed_on_a_throttle_not_every_tick(
        self, child: Any, monkeypatch: Any
    ) -> None:
        """`os.kill` is free and a `ps` is not, and the daemon asks this once
        a second for as long as a conversation is open."""
        reads: list[int] = []
        watch = owner.Watch(owner.Owner(pid=child.pid, started="whatever"))

        def counted(pid: int) -> Any:
            reads.append(pid)
            return None

        monkeypatch.setattr(owner, "read", counted)
        for tick in range(30):
            watch.gone(float(tick))

        assert reads == []

    def test_an_owner_with_no_start_time_is_judged_on_the_pid_alone(self, child: Any) -> None:
        """A platform that would not say. Weaker, and still an answer."""
        watch = owner.Watch(owner.Owner(pid=child.pid, started=""))

        assert watch.gone(owner.RECHECK_EVERY * 10) is False

        child.kill()
        child.wait(timeout=5)

        assert watch.gone(owner.RECHECK_EVERY * 20) is True


@needs_a_process_table
class TestWhereThereIsNoProc:
    """The macOS and BSD path, driven on Linux.

    `ps` is there on every platform this runs on, and its `-o` fields are
    POSIX, so the branch can be taken here and still be a real run of the
    parsing — which is the part with something to get wrong: `lstart` is five
    whitespace-separated fields and the command after it may contain spaces.
    """

    def test_it_reads_a_process_through_ps(self, monkeypatch: Any, child: Any) -> None:
        monkeypatch.setattr(owner, "_platform", lambda: "darwin")

        found = owner.read(child.pid)

        assert found is not None
        assert found.pid == child.pid
        assert found.ppid == os.getpid()
        assert len(found.started.split()) == 5
        assert "python" in found.name

    def test_a_pid_that_is_gone_reads_as_nothing(self, monkeypatch: Any, child: Any) -> None:
        """`ps` exits non-zero rather than printing anything, which is the
        only signal there is that the row was not found."""
        monkeypatch.setattr(owner, "_platform", lambda: "darwin")
        child.kill()
        child.wait(timeout=5)

        assert owner.read(child.pid) is None

    def test_a_start_time_that_does_not_move_across_midnight(
        self, monkeypatch: Any, child: Any
    ) -> None:
        """`lstart` and not `start`: the latter prints a time for today and a
        date for anything older, so a daemon that outlived midnight would
        watch its owner's identity change and end a live session."""
        monkeypatch.setattr(owner, "_platform", lambda: "darwin")
        first = owner.read(child.pid)
        second = owner.read(child.pid)

        assert first is not None and second is not None
        assert first.started == second.started
        assert first.started.split()[-1].isdigit(), "lstart ends in a year"


class TestOnAPlatformThatWillNotSay:
    def test_windows_reports_no_owner(self, monkeypatch: Any) -> None:
        """So the daemon keeps the five-minute window there. Deliberate: an
        owner check that is wrong puts out a live card, and there is no
        Windows run in this suite to be wrong on."""
        monkeypatch.setattr(sys, "platform", "win32")

        assert owner.read(os.getpid()) is None
        assert owner.find() is None
