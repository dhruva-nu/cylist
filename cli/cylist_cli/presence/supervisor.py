"""Starting a daemon, making sure there is only one, and stopping it.

The singleton is an ``flock`` held for the daemon's whole life, not a pidfile.
Two reasons, and the second is the one that matters: a pid can be reused, and
the kernel drops a lock when the process holding it dies *however* it died —
so a daemon killed with SIGKILL leaves no evidence that it is still running,
where a pidfile would.

That property is what makes the rest safe. A hook that fails to take the lock
knows a daemon is alive without asking it anything; a daemon that takes it
knows the socket path is unowned and may unlink whatever is there.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import subprocess
from collections.abc import Sequence
from pathlib import Path

from cylist_cli.presence.ipc import runtime_dir


def lock_path(session_id: str) -> Path:
    from cylist_cli.presence.ipc import socket_path

    return socket_path(session_id).with_suffix(".lock")


def pid_path(session_id: str) -> Path:
    """Where a daemon writes its pid, for ``hook status`` and ``hook stop``.

    Advisory only. The lock is what says whether it is alive; this just says
    whom to signal.
    """
    return lock_path(session_id).with_suffix(".pid")


class Singleton:
    """The lock a daemon holds for as long as it runs."""

    def __init__(self, session_id: str) -> None:
        self.path = lock_path(session_id)
        self._fd: int | None = None

    def acquire(self) -> bool:
        """Take the lock, or report that somebody else has it."""
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            self._fd = None


def is_running(session_id: str) -> bool:
    """Whether a daemon holds this session.

    Answered by trying the lock, so it is true only while a process is
    actually alive — no stale-pid guessing, and nothing to clean up after a
    crash.
    """
    probe = Singleton(session_id)
    if not probe.acquire():
        return True
    probe.release()
    return False


def spawn(command: Sequence[str], session_id: str) -> bool:
    """Start a daemon for this session, unless one is already running.

    Returns whether one was started. Detached with ``start_new_session`` so
    that closing the terminal does not take it with the shell.

    **The DEVNULL on stdout is load-bearing, not hygiene.** Claude Code reads
    a hook's stdout until EOF. A detached child that inherited it would hold
    the pipe open for as long as it lived, and *every* hook invocation would
    appear to hang until its three-second timeout — a symptom about as far
    from its cause as it is possible to get.
    """
    if is_running(session_id):
        return False
    subprocess.Popen(  # noqa: S603 - the command is built from our own argv
        [*command, "hook", "daemon", "--session", session_id],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
        # Not the caller's directory: a daemon outlives the prompt that
        # started it, and holding a cwd open can keep a filesystem mounted.
        cwd="/",
    )
    return True


def stop(session_id: str) -> bool:
    """Ask a daemon to end its session and exit. Returns whether one was there.

    SIGTERM rather than a message on the socket, because this is also the
    answer when the socket is the thing that has gone wrong. The daemon
    treats it as a session ending and says goodbye on its way out.
    """
    try:
        pid = int(pid_path(session_id).read_text().strip())
    except (OSError, ValueError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    return True


def running_sessions() -> list[str]:
    """Every session with a daemon holding its lock, newest first.

    Read off the runtime directory rather than a registry, so a daemon that
    died without tidying up simply does not appear.
    """
    found: list[tuple[float, str]] = []
    try:
        entries = sorted(runtime_dir().glob("*.pid"))
    except OSError:
        return []
    for entry in entries:
        try:
            body = entry.read_text().strip().splitlines()
        except OSError:
            continue
        if len(body) < 2:
            continue
        session = body[1]
        if is_running(session):
            found.append((entry.stat().st_mtime, session))
    return [session for _, session in sorted(found, reverse=True)]


def write_pid(session_id: str) -> None:
    """Record the pid and the session it serves, for status and stop."""
    path = pid_path(session_id)
    path.write_text(f"{os.getpid()}\n{session_id}\n")
    path.chmod(0o600)


def clear_pid(session_id: str) -> None:
    with contextlib.suppress(OSError):
        pid_path(session_id).unlink()
