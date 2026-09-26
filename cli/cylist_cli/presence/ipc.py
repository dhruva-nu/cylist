"""The channel between a hook that lives for milliseconds and a daemon that does not.

One JSON line per connection, with a reply. Not a FIFO, because a dead reader
wedges the writer and there is no way to tell whether anything was heard. Not
the session state file with the daemon watching it, because that is polling —
locally, and with worse latency than the polling this ticket removed.

There are two carriers of that one line, chosen by what the platform has:

* **AF_UNIX** on Linux and macOS. The socket is a channel into a process
  holding a bearer token, so it is created 0600 inside a directory this user
  owns; and where ``XDG_RUNTIME_DIR`` is absent — macOS — the fallback under
  the shared temporary directory is checked for ownership and mode before it
  is trusted, because a symlink planted there by somebody else would
  otherwise be handed the channel. Deliberately not under ``XDG_STATE_HOME``:
  that can be on NFS, where AF_UNIX sockets do not work at all.

* **Loopback TCP** on Windows, which has no ``socket.AF_UNIX`` in CPython.
  A port cannot be given a mode, and any process on the machine may connect
  to 127.0.0.1, so the file permissions the unix socket relies on are
  replaced by a secret: the daemon writes its port and a fresh 256-bit token
  to an endpoint file, and a caller that cannot quote the token back is
  refused before its message is read. The endpoint file lives under
  ``%LOCALAPPDATA%``, which is per-user by the ACL Windows puts on the
  profile directory — that ACL, not a mode bit, is what keeps the token
  private, and it is the same assumption every other token this CLI writes
  already makes.

The Windows carrier is not Windows-only code: ``CYLIST_IPC=tcp`` selects it
anywhere, which is how the suite exercises it on Linux. A path that only ever
runs on the platform nobody develops on is a path that is never really tested.
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import os
import secrets
import socket
import stat
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cylist_cli.presence import protocol

DIR_MODE = 0o700
SOCKET_MODE = 0o600
ENDPOINT_MODE = 0o600

CONNECT_TIMEOUT = 0.25
"""How long a hook will wait on the socket before giving up on it.

Small on purpose. ``UserPromptSubmit`` is on the critical path of every
prompt, the hook's whole budget is three seconds, and there has to be room
left afterwards for the HTTP fallback.
"""

ACCEPT_POLL = 0.05
"""How long the daemon's accept blocks before looking at its stop flag.

Polled rather than left blocking because closing a socket does not reliably
wake an ``accept()`` on another thread — on either platform. The cost is a
sleeping thread waking twenty times a second; the alternative is a daemon
whose exit depends on somebody knocking on the door first.
"""

TRANSPORT_ENV = "CYLIST_IPC"
"""``unix`` or ``tcp``, overriding what the platform would have chosen.

For the suite, and for a Linux sandbox where AF_UNIX is unavailable.
"""


def af_unix() -> int:
    """``socket.AF_UNIX``, which CPython does not define on Windows.

    Reached by name rather than as an attribute so that this module still
    type-checks for a platform that has no such family. Raising ``OSError``
    is the right refusal: every caller is behind :func:`transport`, which
    only says ``"unix"`` where there is one, and ``OSError`` is already what
    both of them treat as "no daemon here" — so even ``CYLIST_IPC=unix`` set
    on Windows degrades to the HTTP path rather than to a traceback.
    """
    family = getattr(socket, "AF_UNIX", None)
    if family is None:
        raise OSError("this platform has no AF_UNIX socket family")
    return int(family)


def transport() -> str:
    """Which carrier this machine uses: ``"unix"`` or ``"tcp"``."""
    forced = os.environ.get(TRANSPORT_ENV, "").strip().lower()
    if forced in {"unix", "tcp"}:
        return forced
    return "unix" if hasattr(socket, "AF_UNIX") else "tcp"


# --- Where the endpoints live ------------------------------------------------


def runtime_dir() -> Path:
    """A private directory for sockets and endpoint files, made if it is not there.

    ``XDG_RUNTIME_DIR`` when the system provides one — it is already 0700 and
    already cleaned up at logout. Then ``%LOCALAPPDATA%`` on Windows, whose
    ACL is what makes it private. Otherwise a per-uid directory under the
    temporary directory, whose ownership and mode are verified rather than
    assumed.

    ``XDG_RUNTIME_DIR`` is honoured on every platform, not only where the
    system sets it, so that a test can redirect this with one environment
    variable and get the same isolation everywhere.
    """
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base and Path(base).is_dir():
        path = Path(base) / "cylist"
    elif sys.platform == "win32":
        path = _local_app_data() / "cylist" / "run"
    else:
        path = Path(tempfile.gettempdir()) / f"cylist-{os.getuid()}"

    path.mkdir(mode=DIR_MODE, parents=True, exist_ok=True)
    _demand_private(path)
    return path


def _local_app_data() -> Path:
    """``%LOCALAPPDATA%``, or where it would be if the variable is missing."""
    root = os.environ.get("LOCALAPPDATA")
    return Path(root) if root else Path.home() / "AppData" / "Local"


def _demand_private(path: Path) -> None:
    """Refuse a directory somebody else could be reading.

    A no-op on Windows, where there are no mode bits to read and ``st_uid``
    is always zero. What stands in for it there is the location: everything
    under the user's profile is ACL-ed to that user, and a directory outside
    it would be a deliberate choice by whoever set ``XDG_RUNTIME_DIR``.
    """
    attributes = path.lstat()
    if not stat.S_ISDIR(attributes.st_mode):
        raise OSError(f"{path} is not a directory")
    if sys.platform != "win32":
        if attributes.st_uid != os.getuid():
            raise OSError(f"{path} belongs to another user")
        if attributes.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            # Tighten rather than refuse: a umask that made it group-readable
            # is a mistake to correct, not an attack to abort on.
            path.chmod(DIR_MODE)


def endpoint_base(session_id: str) -> Path:
    """The extensionless path every per-session file is built from.

    Named by a hash and not by the session id: AF_UNIX paths cap at about a
    hundred bytes on Linux and fewer on macOS, and the directory this sits in
    can already be long.
    """
    digest = hashlib.sha256(session_id.encode()).hexdigest()[:16]
    return runtime_dir() / digest


def socket_path(session_id: str) -> Path:
    """Where a session's daemon listens, under the unix transport."""
    return endpoint_base(session_id).with_suffix(".sock")


def endpoint_path(session_id: str) -> Path:
    """Where a session's daemon records its port and token, under TCP."""
    return endpoint_base(session_id).with_suffix(".endpoint")


def clear_endpoint(session_id: str) -> None:
    """Remove whatever this session's daemon left listening.

    Only sound once the caller knows no daemon holds the session — which the
    singleton lock is what says. A stale endpoint is otherwise harmless: the
    port is refused, the hook falls back, and the next daemon overwrites it.
    """
    for path in (socket_path(session_id), endpoint_path(session_id)):
        with contextlib.suppress(OSError):
            path.unlink()


# --- The hook's side ---------------------------------------------------------


def send(session_id: str, line: str, *, timeout: float = CONNECT_TIMEOUT) -> dict[str, Any] | None:
    """Hand one message to the daemon and read its reply.

    Returns the ack, or ``None`` if there was nobody there, the reply made no
    sense, or it did not arrive in time. Every one of those means the same
    thing to the caller — the daemon cannot be relied on for this event, so
    report over HTTP instead — which is why they are one return value and not
    three exceptions.
    """
    try:
        client, prelude = _dial(session_id, timeout)
    except OSError:
        return None
    try:
        client.sendall(prelude + line.encode())
        with contextlib.suppress(OSError):
            # Best effort. It tells the daemon there is no more to read; a
            # transport that will not half-close is not a reason to give up,
            # because the line already ends in a newline.
            client.shutdown(socket.SHUT_WR)
        reply = _Reader(client).line()
    except OSError:
        return None
    finally:
        client.close()

    ack = protocol.parse_ack(reply) if reply else None
    if ack is None or ack.get("ok") is not True:
        return None
    return ack


def _dial(session_id: str, timeout: float) -> tuple[socket.socket, bytes]:
    """Connect to this session's daemon, and say what must precede the message.

    The prelude is empty over AF_UNIX, where the socket's mode is the
    credential, and the secret line over TCP, where there is not one.
    """
    if transport() == "unix":
        client = socket.socket(af_unix(), socket.SOCK_STREAM)
        client.settimeout(timeout)
        try:
            client.connect(str(socket_path(session_id)))
        except OSError:
            client.close()
            raise
        return client, b""

    port, secret = _read_endpoint(session_id)
    client = socket.create_connection(("127.0.0.1", port), timeout=timeout)
    client.settimeout(timeout)
    return client, (secret + "\n").encode()


def _read_endpoint(session_id: str) -> tuple[int, str]:
    """The port and token a daemon published, or ``OSError`` if there is none.

    Unreadable and absent are the same answer on purpose: both mean there is
    no daemon worth talking to, and ``send`` has one way of saying that.
    """
    path = endpoint_path(session_id)
    try:
        published = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise OSError(f"no endpoint at {path}") from exc
    if not isinstance(published, dict):
        raise OSError(f"{path} is not an endpoint file")
    port = published.get("port")
    secret = published.get("secret")
    if not isinstance(port, int) or not isinstance(secret, str):
        raise OSError(f"{path} is not an endpoint file")
    return port, secret


class _Reader:
    """One line at a time off a stream socket, with a cap on the whole thing."""

    def __init__(self, client: socket.socket) -> None:
        self._client = client
        self._buffer = b""
        self._read = 0

    def line(self) -> str:
        """The next line, without its newline. Empty when there are no more."""
        while b"\n" not in self._buffer and self._read < protocol.MAX_LINE:
            chunk = self._client.recv(4096)
            if not chunk:
                break
            self._buffer += chunk
            self._read += len(chunk)
        head, _, rest = self._buffer.partition(b"\n")
        self._buffer = rest
        return head.decode(errors="replace")


# --- The daemon's side -------------------------------------------------------


class Listener:
    """The daemon's side: a bound endpoint, and one message read per caller."""

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self._stopping = threading.Event()
        self._secret: str | None = None
        if transport() == "unix":
            self.path = socket_path(session_id)
            self._sock = self._bind_unix()
        else:
            self.path = endpoint_path(session_id)
            self._sock = self._bind_loopback()
        self._sock.settimeout(ACCEPT_POLL)
        self._sock.listen(8)

    def _bind_unix(self) -> socket.socket:
        # Safe because the caller holds the singleton lock: nothing else can
        # be listening here, so anything at this path is the corpse of a
        # daemon that did not clean up.
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        bound = socket.socket(af_unix(), socket.SOCK_STREAM)
        bound.bind(str(self.path))
        self.path.chmod(SOCKET_MODE)
        return bound

    def _bind_loopback(self) -> socket.socket:
        """Bind an ephemeral port on the loopback and publish how to reach it.

        ``127.0.0.1`` and never ``0.0.0.0``: this is a channel into a process
        holding a bearer token, and a wildcard bind would put it on the
        network. No ``SO_REUSEADDR`` either — a bind that fails because
        something is already there is a fact worth hearing, not one to
        override.
        """
        bound = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        bound.bind(("127.0.0.1", 0))
        self._secret = secrets.token_urlsafe(32)
        _publish(self.path, {"port": bound.getsockname()[1], "secret": self._secret})
        return bound

    def serve(self, handle: Callable[[dict[str, Any]], str]) -> None:
        """Accept until closed, handing each parsed message to ``handle``.

        Runs on its own thread. Never raises out of a single bad caller: one
        malformed line is that caller's problem, and a daemon that died of it
        would take a live session off the board.
        """
        while not self._stopping.is_set():
            try:
                client, _ = self._sock.accept()
            except TimeoutError:
                continue
            except OSError:
                return  # the socket was closed; the daemon is going away
            try:
                client.settimeout(CONNECT_TIMEOUT)
                client.sendall(self._answer(client, handle).encode())
            except OSError:
                pass
            finally:
                client.close()

    def _answer(self, client: socket.socket, handle: Callable[[dict[str, Any]], str]) -> str:
        reader = _Reader(client)
        if self._secret is not None and not hmac.compare_digest(reader.line(), self._secret):
            # Refused before the message is even parsed. On the loopback the
            # token is the only thing standing between this daemon and any
            # other process on the machine.
            return protocol.refusal("unauthorised")
        message = protocol.parse_message(reader.line())
        return handle(message) if message else protocol.refusal("unreadable")

    def close(self) -> None:
        self._stopping.set()
        self._sock.close()
        with contextlib.suppress(OSError):
            self.path.unlink()


def _publish(path: Path, endpoint: dict[str, Any]) -> None:
    """Write an endpoint file that is never seen half-written.

    Created 0600 and then renamed into place: a hook that read this file
    between the open and the write would find no secret and report over HTTP,
    which is harmless but is a fallback taken for no reason.
    """
    scratch = path.parent / (path.name + ".new")
    descriptor = os.open(scratch, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, ENDPOINT_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(endpoint))
    scratch.replace(path)


def describe(session_id: str) -> dict[str, Any] | None:
    """Ask a daemon what it is doing, for ``cylist hook status``."""
    return send(
        session_id, json.dumps({"v": protocol.VERSION, "t": "ping", "session": session_id}) + "\n"
    )
