"""One persistent connection per bound session, instead of a report per event.

What the hook used to do — open a connection, PUT, close, and do it again a
minute later whether or not anything had changed — is replaced by a small
detached process that holds one WebSocket for as long as the session is on a
card. The hook's job shrinks to handing it a line over a unix socket.

The point is not the saved handshakes. It is that a connection can *end*: a
socket closing tells the board the session is over, where a PUT that stops
coming tells it nothing at all and left the server guessing from a clock.

The five modules, smallest first:

* :mod:`.protocol` — the two wire formats. Pure.
* :mod:`.machine` — every rule about when to send, keepalive and give up.
  Pure, and where to look first.
* :mod:`.ipc` — the unix socket between hook and daemon.
* :mod:`.supervisor` — one daemon per session, started and stopped.
* :mod:`.daemon` — the process. Deliberately dull; it decides nothing.

Only :func:`report` and :func:`finish` are meant to be called from the hook,
and both are written so that failure means "do it the old way" rather than
"raise". The HTTP path stays for exactly that reason: a machine where the
daemon cannot start, or a sandbox with no unix sockets, degrades to what
Cylist did yesterday rather than to silence.

Nothing here may import ``websockets`` at module scope. ``commands`` imports
every module eagerly, and an unbound session — the common case, since hooks
are installed user-wide and fire in every project — must pay nothing.
"""

from __future__ import annotations

import os

from cylist_cli.presence import ipc, protocol, supervisor

MODE_ENV = "CYLIST_PRESENCE"
"""``ws`` to use the daemon, ``http`` for the pre-CYLIST-40 path, ``off`` for
neither.

A permanent switch, not a migration flag. Some people do not want a
background process on their laptop, and "then do not use the board" is not an
answer.
"""


def mode() -> str:
    value = os.environ.get(MODE_ENV, "ws").strip().lower()
    return value if value in {"ws", "http", "off"} else "ws"


def report(
    command: list[str],
    session_id: str,
    task: str,
    state: str,
    reason: str | None,
    client_name: str,
    *,
    bind: bool = False,
) -> bool:
    """Tell the daemon where the session is. False means "fall back to HTTP".

    Starts a daemon if there is not one, but does not wait for it: spawning
    returns in milliseconds and the daemon needs a connection to the server
    before it is any use, which is far longer than a prompt should be held
    up for. So the event that starts a daemon is reported over HTTP, and the
    ones after it go down the socket.
    """
    if mode() != "ws":
        return False

    line = (
        protocol.bind_message(session_id, task, client_name)
        if bind
        else protocol.state_message(session_id, task, state, reason, client_name)
    )
    if ipc.send(session_id, line) is not None:
        return True

    try:
        supervisor.spawn(command, session_id)
    except OSError:
        # No fork, no /tmp, a sandbox that forbids it — all the same answer.
        return False
    return False


def finish(command: list[str], session_id: str, reason: str) -> bool:
    """Tell the daemon the session is over. False means "fall back to HTTP".

    Never spawns. A session that is ending has no use for a new daemon, and
    starting one here would leave a process behind to time out on its own.
    """
    if mode() != "ws":
        return False
    return ipc.send(session_id, protocol.end_message(session_id, reason)) is not None


def daemon_log(session_id: str) -> object:
    """Where a daemon writes what it is doing. Named without importing it.

    ``cylist hook status`` wants to print this path, and importing the daemon
    module to ask would drag ``websockets`` into a command that is only
    listing processes.
    """
    from cylist_cli.commands.hook import sessions_dir

    return sessions_dir().parent / "logs" / f"daemon-{session_id[:16]}.log"


__all__ = [
    "MODE_ENV",
    "daemon_log",
    "finish",
    "ipc",
    "mode",
    "protocol",
    "report",
    "supervisor",
]
