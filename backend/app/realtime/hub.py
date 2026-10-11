"""Who is connected, and how news reaches them.

Two registries, both plain dictionaries in this process. That is not a
shortcut: Cylist runs one uvicorn worker per environment — no ``--workers``,
no replicas — so every socket for a project terminates in the same event loop
and a dictionary is a complete and correct answer. It stops being one the day
a second worker appears, which is why :meth:`Hub.publish` is the single
fan-out entry point and takes a plain JSON-serialisable dict: swapping it for
Postgres ``LISTEN``/``NOTIFY`` is then a change to one method rather than to
every caller. ``DEPLOY.md`` says why that day must not arrive by accident.

**Everything published is a doorbell.** A ``board.changed`` says the board
changed; it does not say what to, and a client answers it by refetching. That
one property is what makes the rest of this module simple enough to trust:

* :meth:`Hub.publish` never awaits a socket, so a browser on a bad connection
  cannot slow down the transaction that is telling it something.
* A nudge already waiting is not queued again, because a second "the board
  changed" adds nothing to the first.
* A dropped event costs a refetch that the next event will trigger anyway.

The cost is one HTTP round trip per nudge. The thing it buys is that there is
exactly one piece of code that knows how to build a card — ``read_tasks()`` —
and no second, drifting copy of it here.

There are two doorbells, not one. ``board.changed`` is the board's; an
``agent.feed`` names one card whose agent feed has grown (CYLIST-88), and only
a browser with that card open does anything about it. They are kept apart
rather than folded into one because an agent writes a feed line every few
seconds, and a board that refetched every card for each would be paying for
a dialog it may not have open.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.realtime import protocol

logger = logging.getLogger(__name__)

_PENDING = 16
"""How many distinct nudges a watcher may have waiting.

Nudges are keyed — ``board.changed`` is one key, each card's ``agent.feed``
another — and a key already waiting is not queued twice: a watcher with a
nudge pending has everything a second one would tell it, so the second is
dropped rather than buffered. That is coalescing, and with the cap it is the
reason a slow client cannot make this grow without bound. The board's own
nudge is always let in; past the cap, it is the feed nudges that wait for the
next one, and a board nudge refetches the open card's feed anyway.

Only messages that are safe to drop and safe to duplicate may go through
:meth:`Hub.publish`. Anything else needs its own path.
"""

BOARD_CHANGED = "board.changed"


@dataclass(eq=False)
class Watcher:
    """One browser watching one project's board.

    ``eq=False`` so watchers are compared by identity: two tabs on the same
    board are two watchers, and a set of them holds both.
    """

    project_id: UUID
    _pending: dict[str, dict[str, Any]] = field(default_factory=dict)
    _ready: asyncio.Event = field(default_factory=asyncio.Event)

    def offer(self, key: str, nudge: dict[str, Any]) -> None:
        """Queue a nudge unless one like it is already waiting. Never blocks."""
        if key in self._pending:
            return
        if key != BOARD_CHANGED and len(self._pending) >= _PENDING:
            return
        self._pending[key] = nudge
        self._ready.set()

    async def next(self) -> dict[str, Any]:
        """The oldest waiting nudge, once there is one."""
        while not self._pending:
            self._ready.clear()
            await self._ready.wait()
        key = next(iter(self._pending))
        return self._pending.pop(key)


@dataclass(eq=False)
class AgentLink:
    """One agent session holding one socket."""

    client_session_id: str
    displaced: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the same client session connects again somewhere else. The
    handler that sees it closes without ending the database row, because the
    row now belongs to the newer socket."""


class Hub:
    """The connections this process is holding, and the way to nudge them."""

    def __init__(self) -> None:
        self._watchers: dict[UUID, set[Watcher]] = {}
        self._agents: dict[str, AgentLink] = {}

    # --- Browsers ----------------------------------------------------------

    @contextmanager
    def watch(self, project_id: UUID) -> Iterator[Watcher]:
        """Register a watcher for as long as the block runs."""
        watcher = Watcher(project_id=project_id)
        self._watchers.setdefault(project_id, set()).add(watcher)
        try:
            yield watcher
        finally:
            watchers = self._watchers.get(project_id)
            if watchers is not None:
                watchers.discard(watcher)
                if not watchers:
                    del self._watchers[project_id]

    def publish(self, event: dict[str, Any]) -> None:
        """Nudge everyone watching the project this event belongs to.

        Synchronous, and it never awaits: it is called from the code that has
        just committed a transaction, and that code is not waiting on anyone's
        network. An event for a project nobody is watching is dropped, which
        is the common case and costs a dictionary lookup.
        """
        project_id = event.get("project_id")
        if not isinstance(project_id, UUID):
            return
        watchers = self._watchers.get(project_id)
        if not watchers:
            return

        task_id = event.get("task_id")
        if event.get("type") == protocol.FEED_CHANGED and isinstance(task_id, UUID):
            key = f"{protocol.FEED_CHANGED}:{task_id}"
            nudge = protocol.feed_changed(str(task_id))
        else:
            key = BOARD_CHANGED
            nudge = protocol.board_changed()
        for watcher in watchers:
            # A watcher with one waiting already has everything this would say.
            watcher.offer(key, nudge)

    # --- Agents ------------------------------------------------------------

    def register_agent(self, link: AgentLink) -> AgentLink | None:
        """Take ownership of a client session, displacing any older socket.

        Last writer wins. The alternative — refusing the new socket — reads as
        safer and is worse: the socket being held may be a half-dead TCP
        connection that ping/pong will not give up on for another forty
        seconds, and the agent trying to reconnect is the one telling the
        truth about where it is.

        Returns the link that was displaced, if there was one, already marked
        so its handler knows to leave the database row alone.
        """
        previous = self._agents.get(link.client_session_id)
        if previous is not None:
            previous.displaced.set()
            logger.info(
                "Agent session %r reconnected; displacing the socket it left",
                link.client_session_id,
            )
        self._agents[link.client_session_id] = link
        return previous

    def release_agent(self, link: AgentLink) -> None:
        """Let go of a socket, unless a newer one has already taken over."""
        if self._agents.get(link.client_session_id) is link:
            del self._agents[link.client_session_id]

    def live_session_ids(self) -> frozenset[str]:
        """The client sessions holding a socket right now.

        What the reaper checks before ending a quiet row: a session in here is
        witnessed, however long it has been since it last said anything.
        """
        return frozenset(self._agents)

    # --- Looking in --------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Counts, for ``/health/realtime``. No names, no content."""
        return {
            "agent_sockets": len(self._agents),
            "board_watchers": sum(len(group) for group in self._watchers.values()),
            "projects_watched": len(self._watchers),
        }
