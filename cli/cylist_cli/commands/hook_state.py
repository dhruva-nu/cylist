"""What the hook remembers between invocations, which is very little.

A hook lives for milliseconds, so anything it knows about a session past the
event in hand is on disk: one small JSON file per session under
``$XDG_STATE_HOME/cylist/sessions`` (``%LOCALAPPDATA%\\cylist\\sessions`` on
Windows) saying which card the session is bound to and whether it has been
nudged. ``/clear`` ends one session and starts another in the same process; a
``handoff.json`` written on the way out and read on the way in is what
carries the binding across.

Every read here treats a missing or unreadable file as "nothing remembered":
the hook must never fail a prompt because a state file was half-written or
deleted under it.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cylist_cli import system

HANDOFF_TTL = timedelta(seconds=5)
"""How old a ``/clear`` handoff may be and still be believed."""


def now() -> datetime:
    return datetime.now(UTC)


def sessions_dir() -> Path:
    """Where a session's binding is remembered between hook invocations.

    One rule for every machine-local file this CLI writes, so that a Windows
    install does not keep its sessions under ``%LOCALAPPDATA%`` and the
    address it last reached under ``~/.local/state``. See
    :func:`cylist_cli.system.state_dir`.
    """
    return system.state_dir() / "sessions"


def state_path(session_id: str) -> Path:
    # A session id is a UUID from the harness, but it is still a path segment
    # from somebody else's process: keep it to one flat file name.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return sessions_dir() / f"{safe}.json"


def load_state(session_id: str) -> dict[str, Any]:
    return _read_object(state_path(session_id)) or {}


def save_state(session_id: str, state: dict[str, Any]) -> None:
    system.write_private(state_path(session_id), json.dumps(state))


def delete_state(session_id: str) -> None:
    state_path(session_id).unlink(missing_ok=True)


def forget_asking(session_id: str, prompt_id: str) -> None:
    """Take a finished permission's mark off the session, if it is still there.

    Read afresh rather than from the copy the hook loaded: other hooks may
    have written the file while this one waited, and a session that ended
    meanwhile has no file to bring back.
    """
    state = load_state(session_id)
    if state.get("asking") != prompt_id:
        return
    del state["asking"]
    save_state(session_id, state)


def handoff_path() -> Path:
    return sessions_dir() / "handoff.json"


def write_handoff(task: str) -> None:
    system.write_private(handoff_path(), json.dumps({"task": task, "at": now().isoformat()}))


def take_handoff() -> str | None:
    """The card the session that just ``/clear``-ed was on, if that was seconds ago.

    Consumed whether or not it is believed: a handoff is for the one session
    that starts straight after, and a stale one left lying about would bind
    the next ``/clear`` somebody does an hour later. The caller checks that
    what comes back is a card reference.
    """
    path = handoff_path()
    try:
        carried = _read_object(path)
    finally:
        path.unlink(missing_ok=True)
    if carried is None:
        return None
    try:
        fresh = now() - datetime.fromisoformat(str(carried.get("at"))) <= HANDOFF_TTL
    except (ValueError, TypeError):  # unparseable, or a timestamp with no zone
        return None
    task = str(carried.get("task") or "")
    return task if fresh and task else None


def _read_object(path: Path) -> dict[str, Any] | None:
    """A JSON object from ``path``, or ``None`` if there is not one there."""
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return loaded if isinstance(loaded, dict) else None
