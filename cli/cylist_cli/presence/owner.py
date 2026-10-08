"""Whose session this is: the ``claude`` process the daemon belongs to.

A daemon exists to say that one conversation is still open. Until this
module, the only evidence it had was noise on the socket — so a session that
was *waiting on its human* looked exactly like a session that had been
killed, and after five minutes the card went out while the conversation was
still on screen. With ten sessions open on one machine, which is ordinary,
nine of the ten cards were wrong.

The evidence that actually answers the question is the harness process
itself. While ``claude`` is running the conversation is open, whatever it is
or is not saying; when it exits the conversation is over, however recently it
spoke. So the hook finds the ``claude`` it was run by and hands it to the
daemon, and the daemon watches it.

**A pid is not an identity.** Pids are recycled, and a daemon can outlive the
wrap-around on a busy machine; "pid 912470 exists" would then be true of some
unrelated process and the card would stay lit forever. So an owner is a pid
*and* the moment that pid started, which together name one process for as
long as it runs and match nothing afterwards.

What a platform cannot answer, it says nothing about: :func:`find` returns
``None`` and the daemon keeps the five-minute idle rule it has always had.
That is the one guarantee worth more than accuracy here — a card must never
be able to stay lit forever.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

NAMES = frozenset({"claude", "claude.exe", "claude.cmd"})
"""Executable names that mean "the harness". Compared without the extension
too, so a ``claude.js`` under a node install is recognised as well."""

MAX_DEPTH = 12
"""How far up the ancestry to look before concluding this was not started by
``claude`` at all.

A hook runs two or three processes below the harness — ``claude`` spawns a
shell, the shell runs ``cylist``. A dozen is room for a wrapper, a ``uv``
shim and a multiplexer without ever walking to pid 1 on a machine where the
answer is simply "no".
"""

RECHECK_EVERY = 60.0
"""Seconds between confirmations that the pid is still the *same* process.

Liveness is asked on every tick, because ``os.kill(pid, 0)`` costs nothing.
Identity is a file read on Linux and a ``ps`` on macOS, and asking it once a
minute is enough: for a stale pid to be believed, the owner must die *and*
the machine must cycle its whole pid space inside this window.
"""

PS_TIMEOUT = 2.0
"""How long to wait on ``ps``. It is run at most once a minute and never on
the prompt's critical path, but a daemon must not be wedged by it."""


@dataclass(frozen=True)
class Owner:
    """One process, named in a way a recycled pid cannot imitate."""

    pid: int
    started: str
    """When it started, in whatever form this platform reports.

    Opaque on purpose: Linux counts clock ticks since boot, ``ps`` prints a
    date. Nothing compares these to anything but another reading of the same
    pid on the same machine, so there is nothing to be gained by parsing it
    and a portability problem to be had by trying.

    Empty means the platform would not say. The pid alone is then the
    identity, which is weaker but still far better than a clock.
    """


@dataclass(frozen=True)
class Process:
    """One row of the process table, as this module needs it."""

    pid: int
    ppid: int
    name: str
    started: str


def find(start: int | None = None) -> Owner | None:
    """The nearest ``claude`` at or above ``start``, or ``None``.

    Walks parents rather than trusting a name match on the first process it
    sees, because what runs the hook is a shell and what runs the shell is
    the harness. ``CLAUDE_PID`` is used when the harness sets it, but only
    after it has been found in the ancestry — an inherited variable from an
    outer session names the wrong process, and that is the one way this could
    silently keep a card lit after its session had gone.
    """
    pid = os.getpid() if start is None else start
    hinted = _hinted_pid()
    chain: list[Process] = []
    seen: set[int] = set()

    for _ in range(MAX_DEPTH):
        if pid <= 1 or pid in seen:
            break
        seen.add(pid)
        process = read(pid)
        if process is None:
            break
        chain.append(process)
        pid = process.ppid

    for process in chain:
        if process.pid == hinted or _is_harness(process.name):
            return Owner(pid=process.pid, started=process.started)
    return None


def _hinted_pid() -> int | None:
    """``CLAUDE_PID``, when the harness sets it and it is a number."""
    raw = os.environ.get("CLAUDE_PID", "").strip()
    try:
        return int(raw)
    except ValueError:
        return None


def _is_harness(name: str) -> bool:
    """Whether a process name is the harness.

    The bare name, and the name with its extension removed — ``claude.exe``
    on Windows, and a node install whose argv names ``claude.js``.
    """
    base = Path(name).name
    return base in NAMES or Path(base).stem in NAMES


class Watch:
    """Whether one owner is still there, asked as often as you like.

    Holds the throttle for the identity check, so the daemon's loop can ask
    on every tick without caring what each platform charges for an answer.
    """

    def __init__(self, owner: Owner, now: float = 0.0) -> None:
        self.owner = owner
        self._checked = now

    def gone(self, now: float) -> bool:
        """True once the owner has exited — or been replaced by a reused pid."""
        if not _running(self.owner.pid):
            return True
        if not self.owner.started or now - self._checked < RECHECK_EVERY:
            return False
        self._checked = now
        process = read(self.owner.pid)
        if process is None:
            # Alive a moment ago and unreadable now: it exited between the two
            # questions, or this platform stopped answering. Either way the
            # five-minute window is still underneath us, so saying "here" is
            # the conservative answer and not a way to be wrong forever.
            return False
        return process.started != self.owner.started


def _running(pid: int) -> bool:
    """Whether anything holds this pid.

    One ``except`` and not three, because every refusal means the same thing
    here. ``ProcessLookupError`` is the plain answer; ``PermissionError``
    counts as gone too, since the owner runs as this user and a pid we may no
    longer signal is a pid that has been given to somebody else.
    """
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


# --- Reading the process table ----------------------------------------------


def _platform() -> str:
    """``sys.platform``, asked now and as a plain string.

    Both halves matter. *Now*, because the suite reaches every branch below
    by patching ``sys.platform`` at call time — the same reason
    :func:`cylist_cli.system.windows` is a function. And *as a string*,
    because mypy resolves a literal ``sys.platform == "win32"`` against the
    platform it is checking on and then calls the other branches dead code,
    which is a worse outcome than the import it is trying to protect.
    """
    return sys.platform


def read(pid: int) -> Process | None:
    """One process, or ``None`` if this platform or this pid will not say."""
    if _platform() == "win32":
        # No /proc and no ps. There is a Win32 answer — a toolhelp snapshot
        # and `GetProcessTimes` — but it is ctypes that no run on any machine
        # we develop or test on would ever execute, and an owner check that
        # is wrong is worse than none: it clears a live card. Windows keeps
        # the five-minute rule until this can be written against a real
        # Windows run.
        return None
    if _platform() == "linux":
        return _read_proc(pid)
    return _read_ps(pid)


def _read_proc(pid: int) -> Process | None:
    """``/proc/<pid>/stat``: no fork, no shell, and exact.

    Parsed from the closing parenthesis backwards. The second field is the
    executable name and may itself contain spaces and brackets — ``(foo bar)``
    is a legal comm — so splitting the line on whitespace is wrong in exactly
    the case that matters, a process someone has renamed.
    """
    try:
        line = Path(f"/proc/{pid}/stat").read_text("utf-8", errors="replace")
    except OSError:
        return None
    head, _, rest = line.partition("(")
    name, _, tail = rest.rpartition(")")
    fields = tail.split()
    # After the comm, `stat` is: state, ppid, ... — so ppid is the second and
    # starttime the twentieth of what is left. Numbered from the manual page's
    # field 3, which is the state.
    if len(fields) < 20 or not head.strip().isdigit():
        return None
    return Process(pid=pid, ppid=int(fields[1]), name=name, started=fields[19])


def _read_ps(pid: int) -> Process | None:
    """``ps`` for macOS and the BSDs, which have no ``/proc``.

    ``lstart`` rather than ``start``: the latter prints a time for today and
    a date for anything older, so a daemon that outlived midnight would see
    the owner's identity change under it and end a live session.
    """
    try:
        done = subprocess.run(  # noqa: S603 - a fixed argv, no shell
            ["ps", "-o", "ppid=,lstart=,comm=", "-p", str(pid)],  # noqa: S607 - ps is in PATH
            capture_output=True,
            text=True,
            timeout=PS_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    # `ppid`, then `lstart`'s five fields ("Tue Oct  7 09:41:02 2026"), then
    # the command — which is a path and may contain spaces, so it is whatever
    # is left rather than one more token.
    fields = done.stdout.strip().split()
    if len(fields) < 7 or not fields[0].isdigit():
        return None
    return Process(
        pid=pid,
        ppid=int(fields[0]),
        name=" ".join(fields[6:]),
        started=" ".join(fields[1:6]),
    )
