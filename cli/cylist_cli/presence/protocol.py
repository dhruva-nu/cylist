"""The two wire formats a bound session uses, and nothing that touches them.

Pure: no sockets, no files, no clock. Everything here is a function of its
arguments, which is what makes the lifecycle testable without a network.

**Hook to daemon** — AF_UNIX, one JSON object per line, one object per
connection, and one reply::

    {"v": 1, "t": "state", "session": "…", "task": "ATL-41",
     "state": "working", "reason": null, "client_name": "ATL-41"}
    {"v": 1, "t": "bind",  "session": "…", "task": "ATL-42", "client_name": "ATL-42"}
    {"v": 1, "t": "end",   "session": "…", "reason": "session_ended"}
    {"v": 1, "ok": true, "link": "live", "task": "ATL-41", "pid": 12345}

The session id is echoed in every message and checked by the daemon. It costs
nothing and it means a reused runtime directory, or a hash collision in a
socket name, cannot silently deliver one conversation's state to another's
socket.

**Daemon to server** — mirrors ``backend/app/realtime/protocol.py``, which is
the authoritative definition. WebSocket routes are not in the OpenAPI
document, so nothing generates this and the two change together.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlsplit, urlunsplit

VERSION = 1
"""Bumped when a message shape changes incompatibly.

The daemon echoes its version in every ack, so a hook from a newer CLI can
tell it is talking to a daemon started by an older one — which happens
whenever the CLI is upgraded while a session is open.
"""

MAX_LINE = 64 * 1024
"""Longest IPC line to read. Nothing legitimate is close."""

WS_PATH = "/api/v1/agent-sessions/{session}/ws"


def ws_url(base: str, session_id: str) -> str:
    """Where this session's socket lives, from the configured HTTP base URL."""
    split = urlsplit(base)
    scheme = "wss" if split.scheme == "https" else "ws"
    path = split.path.rstrip("/") + WS_PATH.format(session=session_id)
    return urlunsplit((scheme, split.netloc, path, "", ""))


# --- Hook to daemon --------------------------------------------------------


def state_message(session: str, task: str, state: str, reason: str | None, name: str) -> str:
    return _line(
        {
            "t": "state",
            "session": session,
            "task": task,
            "state": state,
            "reason": reason,
            "client_name": name,
        }
    )


def bind_message(session: str, task: str, name: str) -> str:
    return _line({"t": "bind", "session": session, "task": task, "client_name": name})


def end_message(session: str, reason: str) -> str:
    return _line({"t": "end", "session": session, "reason": reason})


def ack(link: str, task: str | None, pid: int) -> str:
    return _line({"ok": True, "link": link, "task": task, "pid": pid})


def refusal(why: str) -> str:
    return _line({"ok": False, "why": why})


def _line(body: dict[str, Any]) -> str:
    return json.dumps({"v": VERSION, **body}) + "\n"


def parse_message(raw: str) -> dict[str, Any] | None:
    """Read one hook-to-daemon line, or ``None`` if it is not one.

    ``None`` rather than an exception because the daemon's answer to every
    unreadable line is the same — refuse it and carry on — and because a
    daemon that could be killed by a stray byte on its socket would be worse
    than no daemon.
    """
    if len(raw.encode()) > MAX_LINE:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict) or parsed.get("t") not in {"state", "bind", "end", "ping"}:
        return None
    if not isinstance(parsed.get("session"), str):
        return None
    return parsed


def parse_ack(raw: str) -> dict[str, Any] | None:
    """Read the daemon's reply, or ``None`` if it is unreadable."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# --- Daemon to server ------------------------------------------------------


def hello_frame(task: str, name: str, state: str, reason: str | None) -> str:
    """The full current level, sent on connect and on every reconnect.

    A resync, never a replay: the server is told where the session *is*, not
    the sequence of states it passed through while the socket was down. That
    is the whole reconnection protocol.
    """
    return json.dumps(
        {
            "type": "state",
            "task": task,
            "report": {"state": state, "reason": reason, "client_name": name},
        }
    )


def heartbeat_frame() -> str:
    return json.dumps({"type": "heartbeat"})


def bye_frame(reason: str) -> str:
    return json.dumps({"type": "bye", "reason": reason})
