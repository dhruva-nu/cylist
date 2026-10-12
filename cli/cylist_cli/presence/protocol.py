"""The two wire formats a bound session uses, and nothing that touches them.

Pure: no sockets, no files, no clock. Everything here is a function of its
arguments, which is what makes the lifecycle testable without a network.

**Hook to daemon** — AF_UNIX, one JSON object per line, one object per
connection, and one reply::

    {"v": 1, "t": "state", "session": "…", "task": "ATL-41",
     "state": "working", "reason": null, "client_name": "ATL-41",
     "note": "Running pytest test_pr_states.py", "transcript": "/…/abc.jsonl"}
    {"v": 1, "t": "bind",  "session": "…", "task": "ATL-42", "client_name": "ATL-42",
     "transcript": "/…/abc.jsonl"}
    {"v": 1, "t": "end",   "session": "…", "reason": "session_ended"}
    {"v": 1, "t": "ask",   "session": "…", "task": "ATL-41", "client_name": "ATL-41",
     "prompt_id": "…", "text": "Running git push origin staging", "tool": "Bash",
     "transcript": "/…/abc.jsonl"}
    {"v": 1, "ok": true, "link": "live", "task": "ATL-41", "pid": 12345}

``ask`` is the one message whose reply is not immediate (CYLIST-89). The
``PermissionRequest`` hook sends it and holds the connection open — no
half-close, so the daemon can tell the hook being killed from the hook
waiting — until a person answers on the board, the daemon's own wait runs
out, a ``ran`` says the terminal answered first, or the hook goes away. Its ack
carries ``"answer": {"status", "key", "answer"}``, or ``null`` when nobody
here answered.

``ran`` is the ``PostToolUse`` hook of a session with a permission still
waiting on the board: the tool ran, so the terminal's dialog was answered
first. Claude Code does not end the ``PermissionRequest`` hook when that
happens, so without this the prompt would stay on the card until the
session next reported anything.

``transcript`` is the path Claude Code gives every hook event, and how the
daemon learns which file to follow for the card's feed (CYLIST-88). Optional:
a daemon told none simply sends no feed.

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


def state_message(
    session: str,
    task: str,
    state: str,
    reason: str | None,
    name: str,
    note: str | None = None,
    transcript: str | None = None,
) -> str:
    return _line(
        {
            "t": "state",
            "session": session,
            "task": task,
            "state": state,
            "reason": reason,
            "client_name": name,
            "note": note,
            "transcript": transcript,
        }
    )


def bind_message(session: str, task: str, name: str, transcript: str | None = None) -> str:
    return _line(
        {
            "t": "bind",
            "session": session,
            "task": task,
            "client_name": name,
            "transcript": transcript,
        }
    )


def end_message(session: str, reason: str) -> str:
    return _line({"t": "end", "session": session, "reason": reason})


def ask_message(
    session: str,
    task: str,
    name: str,
    prompt_id: str,
    text: str,
    tool: str | None,
    transcript: str | None = None,
) -> str:
    return _line(
        {
            "t": "ask",
            "session": session,
            "task": task,
            "client_name": name,
            "prompt_id": prompt_id,
            "text": text,
            "tool": tool,
            "transcript": transcript,
        }
    )


def ran_message(session: str) -> str:
    """A tool just ran, so any permission still on the card was answered in the terminal."""
    return _line({"t": "ran", "session": session})


def ack(link: str, task: str | None, pid: int, answer: dict[str, Any] | None = None) -> str:
    body: dict[str, Any] = {"ok": True, "link": link, "task": task, "pid": pid}
    if answer is not None:
        body["answer"] = answer
    return _line(body)


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
    if not isinstance(parsed, dict) or parsed.get("t") not in MESSAGE_KINDS:
        return None
    if not isinstance(parsed.get("session"), str):
        return None
    if parsed["t"] == "ask" and not (
        isinstance(parsed.get("prompt_id"), str) and isinstance(parsed.get("text"), str)
    ):
        return None
    return parsed


MESSAGE_KINDS = frozenset({"state", "bind", "end", "ping", "ask", "ran"})


def parse_ack(raw: str) -> dict[str, Any] | None:
    """Read the daemon's reply, or ``None`` if it is unreadable."""
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


# --- Daemon to server ------------------------------------------------------


def hello_frame(
    task: str, name: str, state: str, reason: str | None, note: str | None = None
) -> str:
    """The full current level, sent on connect and on every reconnect.

    A resync, never a replay: the server is told where the session *is*, not
    the sequence of states it passed through while the socket was down. That
    is the whole reconnection protocol.

    ``note`` is what the session is doing right now — its current tool call —
    and goes with every level, because the server replaces it on every report.
    """
    return json.dumps(
        {
            "type": "state",
            "task": task,
            "report": {"state": state, "reason": reason, "client_name": name, "note": note},
        }
    )


FEED_BATCH = 20
"""Most lines per ``feed`` frame. The server takes up to 50; at twenty, with
every line clipped, a frame stays far inside its 64 KiB ceiling."""


def feed_frames(task: str, entries: list[dict[str, Any]]) -> list[str]:
    """What the session did, as as many ``feed`` frames as it takes.

    Not acknowledged by the server, and not part of the level: a feed line is
    sent once, while the link is up, and a reconnect does not replay them.
    """
    return [
        json.dumps({"type": "feed", "task": task, "entries": entries[i : i + FEED_BATCH]})
        for i in range(0, len(entries), FEED_BATCH)
    ]


def prompt_frame(task: str, prompt_id: str, text: str, tool: str | None) -> str:
    """A permission Claude Code wants, for the card. See ``ask`` above."""
    return json.dumps(
        {
            "type": "prompt",
            "task": task,
            "prompt": {"id": prompt_id, "kind": "permission", "text": text, "tool": tool},
        }
    )


def withdraw_frame(task: str, prompt_id: str, reason: str) -> str:
    """The session stopped waiting on a prompt: ``answered_in_terminal`` or ``expired``."""
    return json.dumps({"type": "withdraw", "task": task, "prompt_id": prompt_id, "reason": reason})


def parse_server_frame(raw: str | bytes) -> dict[str, Any] | None:
    """A frame the server sent, or ``None`` if it is not one worth reading.

    Acks, ``ready`` and errors are all frames the daemon has always let pass
    unread; an ``answer`` — a permission closed on the board — is the one it
    acts on, and is returned only when it says which prompt it closes.
    """
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, dict):
        return None
    if parsed.get("type") == "answer" and not isinstance(parsed.get("prompt_id"), str):
        return None
    return parsed


def heartbeat_frame() -> str:
    return json.dumps({"type": "heartbeat"})


def bye_frame(reason: str) -> str:
    return json.dumps({"type": "bye", "reason": reason})
