"""The channel between a hook that lives for milliseconds and a daemon that does not.

An AF_UNIX stream socket, one JSON line per connection, with a reply. Not a
FIFO, because a dead reader wedges the writer and there is no way to tell
whether anything was heard. Not the session state file with the daemon
watching it, because that is polling — locally, and with worse latency than
the polling this ticket removed.

Two details are security rather than housekeeping. The socket is a channel
into a process holding a bearer token, so it is created 0600 inside a
directory this user owns; and where ``XDG_RUNTIME_DIR`` is absent — macOS —
the fallback under the shared temporary directory is checked for ownership
and mode before it is trusted, because a symlink planted there by somebody
else would otherwise be handed the channel.

Deliberately not under ``XDG_STATE_HOME``: that can be on NFS, where AF_UNIX
sockets do not work at all.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import socket
import stat
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cylist_cli.presence import protocol

DIR_MODE = 0o700
SOCKET_MODE = 0o600

CONNECT_TIMEOUT = 0.25
"""How long a hook will wait on the socket before giving up on it.

Small on purpose. ``UserPromptSubmit`` is on the critical path of every
prompt, the hook's whole budget is three seconds, and there has to be room
left afterwards for the HTTP fallback.
"""


def runtime_dir() -> Path:
    """A private directory for sockets, made if it is not there.

    ``XDG_RUNTIME_DIR`` when the system provides one — it is already 0700 and
    already cleaned up at logout. Otherwise a per-uid directory under the
    temporary directory, whose ownership and mode are verified rather than
    assumed.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).is_dir():
        path = Path(base) / "cylist"
    else:
        path = Path(tempfile.gettempdir()) / f"cylist-{os.getuid()}"

    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    _demand_private(path)
    return path


def _demand_private(path: Path) -> None:
    """Refuse a directory somebody else could be reading."""
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode):
        raise OSError(f"{path} is not a directory")
    if info.st_uid != os.getuid():
        raise OSError(f"{path} belongs to another user")
    if info.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        # Tighten rather than refuse: a umask that made it group-readable is
        # a mistake to correct, not an attack to abort on.
        path.chmod(DIR_MODE)


def socket_path(session_id: str) -> Path:
    """Where a session's daemon listens.

    Named by a hash and not by the session id: AF_UNIX paths cap at about a
    hundred bytes on Linux and fewer on macOS, and the directory this sits in
    can already be long.
    """
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return runtime_dir() / f"{digest}.sock"


def send(session_id: str, line: str, *, timeout: float = CONNECT_TIMEOUT) -> dict[str, Any] | None:
    """Hand one message to the daemon and read its reply.

    Returns the ack, or ``None`` if there was nobody there, the reply made no
    sense, or it did not arrive in time. Every one of those means the same
    thing to the caller — the daemon cannot be relied on for this event, so
    report over HTTP instead — which is why they are one return value and not
    three exceptions.
    """
    path = socket_path(session_id)
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.settimeout(timeout)
    try:
        client.connect(str(path))
        client.sendall(line.encode())
        client.shutdown(socket.SHUT_WR)
        reply = _read_line(client)
    except OSError:
        return None
    finally:
        client.close()

    ack = protocol.parse_ack(reply) if reply else None
    if ack is None or ack.get("ok") is not True:
        return None
    return ack


def _read_line(client: socket.socket) -> str:
    chunks: list[bytes] = []
    total = 0
    while total < protocol.MAX_LINE:
        chunk = client.recv(4096)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        if b"\n" in chunk:
            break
    return b"".join(chunks).decode(errors="replace")


class Listener:
    """The daemon's side: a bound socket, and one message read per caller."""

    def __init__(self, session_id: str) -> None:
        self.path = socket_path(session_id)
        # Safe because the caller holds the singleton lock: nothing else can
        # be listening here, so anything at this path is the corpse of a
        # daemon that did not clean up.
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        self.path.chmod(SOCKET_MODE)
        self._sock.listen(8)

    def serve(self, handle: Callable[[dict[str, Any]], str]) -> None:
        """Accept forever, handing each parsed message to ``handle``.

        Runs on its own thread. Never raises out of a single bad caller: one
        malformed line is that caller's problem, and a daemon that died of it
        would take a live session off the board.
        """
        while True:
            try:
                client, _ = self._sock.accept()
            except OSError:
                return  # the socket was closed; the daemon is going away
            try:
                client.settimeout(CONNECT_TIMEOUT)
                message = protocol.parse_message(_read_line(client))
                reply = handle(message) if message else protocol.refusal("unreadable")
                client.sendall(reply.encode())
            except OSError:
                pass
            finally:
                client.close()

    def close(self) -> None:
        self._sock.close()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()


def describe(session_id: str) -> dict[str, Any] | None:
    """Ask a daemon what it is doing, for ``cylist hook status``."""
    return send(
        session_id, json.dumps({"v": protocol.VERSION, "t": "ping", "session": session_id}) + "\n"
    )
