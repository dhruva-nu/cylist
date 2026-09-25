"""Starting a daemon, making sure there is only one, and stopping it.

The singleton is a byte-range lock held for the daemon's whole life, not a
pidfile. Two reasons, and the second is the one that matters: a pid can be
reused, and the kernel drops a lock when the process holding it dies
*however* it died — so a daemon killed with SIGKILL leaves no evidence that
it is still running, where a pidfile would.

That property is what makes the rest safe. A hook that fails to take the lock
knows a daemon is alive without asking it anything; a daemon that takes it
knows the endpoint path is unowned and may unlink whatever is there.

It is ``flock`` on Linux and macOS and ``msvcrt.locking`` on Windows, which
are the same bargain: advisory, per open file, and released by the kernel
when the process ends by any means. Nothing above this module can tell them
apart.

Stopping is where the platforms genuinely differ. A POSIX daemon is sent
SIGTERM and hears it — see the handler in :mod:`.daemon` — so it says goodbye
on the way out. Windows has no such signal: ``os.kill`` there is
``TerminateProcess``, which runs no handler and no ``finally``. So on Windows
the daemon is asked to end over its own IPC channel first, and terminated
only if that gets no answer — with the files it could not clean up removed
here instead.
"""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from cylist_cli.presence import ipc, protocol

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

STOP_TIMEOUT = 1.0
"""How long ``hook stop`` waits on the IPC channel before terminating.

Longer than a hook's quarter-second, because this is a person waiting at a
prompt rather than a prompt waiting on a hook, and the graceful path is worth
a moment.
"""


def lock_path(session_id: str) -> Path:
    return ipc.endpoint_base(session_id).with_suffix(".lock")


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
            _lock_exclusively(fd)
        except OSError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def release(self) -> None:
        if self._fd is not None:
            with contextlib.suppress(OSError):
                _unlock(self._fd)
                os.close(self._fd)
            self._fd = None


def _lock_exclusively(fd: int) -> None:
    """Lock the file exclusively, without waiting. ``OSError`` if it is taken."""
    if sys.platform == "win32":
        # One byte at offset zero, on a file that may well be empty: Windows
        # locks ranges rather than files and is content to lock past the end.
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(fd: int) -> None:
    if sys.platform == "win32":
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        fcntl.flock(fd, fcntl.LOCK_UN)


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

    Returns whether one was started. Detached, so that closing the terminal
    does not take it with the shell: a new session on POSIX, and
    ``DETACHED_PROCESS`` on Windows — which also means no console window
    blinks open on every prompt, because a daemon with a console is one
    Windows will show.

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
        close_fds=True,
        # Not the caller's directory: a daemon outlives the prompt that
        # started it, and holding a cwd open can keep a filesystem mounted —
        # or, on Windows, a drive letter from being ejected.
        cwd=detached_cwd(),
        **_detach(),
    )
    return True


def _detach() -> dict[str, Any]:
    """The Popen arguments that cut the child loose from this process.

    ``start_new_session`` is POSIX-only and silently ignored on Windows,
    which is the worst of both: it would look set and do nothing.
    """
    if sys.platform == "win32":
        return {
            "creationflags": subprocess.DETACHED_PROCESS
            | subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        }
    else:
        return {"start_new_session": True}


def detached_cwd() -> str:
    """A directory the daemon can hold open without holding anything hostage.

    The filesystem root on POSIX. ``%SystemDrive%`` on Windows, because
    ``/`` there means the root of whichever drive the caller happened to be
    on — and a daemon that outlives the prompt should not be the reason a
    USB stick will not eject.
    """
    if sys.platform == "win32":
        drive = os.environ.get("SYSTEMDRIVE") or Path.home().drive or "C:"
        return drive.rstrip("\\") + "\\"
    else:
        return "/"


def stop(session_id: str) -> bool:
    """Ask a daemon to end its session and exit. Returns whether one was there.

    On POSIX, SIGTERM rather than a message on the socket, because this is
    also the answer when the socket is the thing that has gone wrong. The
    daemon treats it as a session ending and says goodbye on its way out.

    On Windows there is no such signal, so the order is reversed: the socket
    first, because it is the only graceful option, and termination after it —
    which leaves the daemon's own files behind, so they are cleared here.
    """
    if sys.platform == "win32" and _ask_to_end(session_id):
        return True

    try:
        pid = int(pid_path(session_id).read_text().strip().splitlines()[0])
    except (OSError, ValueError, IndexError):
        return False
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        # A pid that is gone, or one this user may not signal. Windows says
        # so with a plain OSError rather than either of the POSIX subclasses,
        # which is why this is the broad catch and not two narrow ones.
        return False
    if sys.platform == "win32":
        # TerminateProcess ran no `finally`: nothing unlinked the endpoint or
        # the pid file, and a hook that found them would dial a dead port.
        clear_pid(session_id)
        ipc.clear_endpoint(session_id)
    return True


def _ask_to_end(session_id: str) -> bool:
    """Tell the daemon over its own channel that the session is over."""
    message = protocol.end_message(session_id, "session_ended")
    return ipc.send(session_id, message, timeout=STOP_TIMEOUT) is not None


def running_sessions() -> list[str]:
    """Every session with a daemon holding its lock, newest first.

    Read off the runtime directory rather than a registry, so a daemon that
    died without tidying up simply does not appear.
    """
    found: list[tuple[float, str]] = []
    try:
        entries = sorted(ipc.runtime_dir().glob("*.pid"))
    except OSError:
        return []
    for entry in entries:
        try:
            lines = entry.read_text().strip().splitlines()
        except OSError:
            continue
        # The pid, then the session it serves: see `write_pid`.
        if len(lines) < 2:
            continue
        session = lines[1]
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
