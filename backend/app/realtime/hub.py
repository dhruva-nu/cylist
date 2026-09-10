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
* A queue that is already full is left alone rather than grown, because a
  second "the board changed" adds nothing to the first.
* A dropped event costs a refetch that the next event will trigger anyway.

The cost is one HTTP round trip per nudge. The thing it buys is that there is
exactly one piece of code that knows how to build a card — ``read_tasks()`` —
and no second, drifting copy of it here.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID

from app.core.clock import now

logger = logging.getLogger(__name__)

_PENDING = 1
"""How many nudges a watcher may have waiting.

One, because they are identical. A watcher with a nudge already queued has
everything a second one would tell it, so the second is dropped rather than
buffered — which is coalescing, and is the reason a slow client cannot make
this grow without bound.

Only messages that are safe to drop and safe to duplicate may go through
:meth:`Hub.publish`. Anything else needs its own path.
"""


@dataclass(eq=False)
class Watcher:
    """One browser watching one project's board.

    ``eq=False`` so watchers are compared by identity: two tabs on the same
    board are two watchers, and a set of them holds both.
    """

    project_id: UUID
    queue: asyncio.Queue[dict[str, Any]] = field(
        default_factory=lambda: asyncio.Queue(maxsize=_PENDING)
    )


@dataclass(eq=False)
class AgentLink:
    """One agent session holding one socket."""

    client_session_id: str
    token_id: UUID | None
    actor_label: str
    connected_at: datetime
    task_reference: str | None = None
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

        nudge = {"type": "board.changed", "at": now().isoformat()}
        for watcher in watchers:
            # A watcher with one queued already has everything this would say.
            with suppress(asyncio.QueueFull):
                watcher.queue.put_nowait(nudge)

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
