"""Which of a server's addresses to try first, remembered between commands.

A Cylist server is often reachable more than one way — ``localhost`` on the
machine it runs on, a tailnet name from a laptop on the tailnet, a public
hostname from anywhere — and :mod:`cylist_cli.config` keeps the whole list
so that leaving the tailnet is not a reconfiguration. That leaves one
question, which is this module: what order to try them in.

The configured order is "cheapest first", which is right when the cheapest
one works and wrong for exactly as long as it takes to time out when it does
not. So the address that answered last time is tried first, and only for a
little while:

* remembering it makes the second command in a row cost nothing extra;
* forgetting it soon means a laptop that has come home goes back to the fast
  local address on its own, rather than routing through a public hostname
  until somebody notices and re-runs setup.

Half an hour is the compromise. Being wrong costs one connection timeout,
twice an hour; not expiring at all costs every request for as long as the
memory stands, which is the worse of the two.

Nothing here may raise. A read-only home directory is a reason to try the
addresses in their configured order, not a reason for a command to fail — and
so is a half-written file, which two commands finishing at the same instant
can produce. An unreadable memory is no memory, which costs the ordering and
nothing else.
"""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cylist_cli import system

MEMORY_TTL = timedelta(minutes=30)
"""How long the address that last worked is believed. See above."""

FILE_MODE = 0o600
"""No secret in here, but it sits beside files that hold one."""


def memory_path() -> Path:
    return system.state_dir() / "endpoint.json"


def order(urls: Sequence[str]) -> tuple[str, ...]:
    """The candidates, with the one that answered last time moved to the front.

    The remembered address has to already be in ``urls``: it is a hint about
    order, never a source of addresses. One that has been removed from the
    configuration is simply not used.
    """
    hint = remembered()
    if hint is None or hint not in urls:
        return tuple(urls)
    return (hint, *(url for url in urls if url != hint))


def remembered() -> str | None:
    """The address that last answered, if that was recently enough."""
    try:
        stored = json.loads(memory_path().read_text("utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(stored, dict):
        return None

    url = stored.get("url")
    if not isinstance(url, str) or not url:
        return None
    try:
        at = datetime.fromisoformat(str(stored.get("at")))
    except ValueError:
        return None
    if at.tzinfo is None:
        return None
    return url if datetime.now(UTC) - at <= MEMORY_TTL else None


def remember(url: str) -> None:
    """Record that ``url`` answered, or refresh the note that it still does."""
    payload = json.dumps({"url": url, "at": datetime.now(UTC).isoformat()})
    path = memory_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(payload)
    except OSError:
        # Nowhere to write it. Every command then pays the configured order,
        # which is the behaviour this module exists to improve rather than to
        # depend on.
        return
