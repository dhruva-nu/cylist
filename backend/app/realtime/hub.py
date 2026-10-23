"""Who is connected, and how news reaches them.

Three registries, all plain dictionaries — and all of *this process's*
connections only. A deployment may run several processes (CYLIST-133), and a
board in one may be watching a card an agent in another is changing; what
joins them is :class:`app.realtime.bus.Bus`, which hands every committed
event to this hub *and* to every other process's, over Postgres
``LISTEN``/``NOTIFY``. So :meth:`Hub.publish` is "tell the connections held
here", called once per process per event, and never has to know how many
processes there are.

Nothing here may decide anything that needs every process's view. "Is this
agent session held by a socket?" used to be answered from ``_agents``, which
was a complete answer for one worker and a wrong one for two; it is now a
column the holding process writes (``agent_session.socket_seen_at``).

**Everything published is a doorbell.** A ``board.changed`` says the board
changed, and at most *which things* — ``{entity_type, entity_id}`` references,
so a client can refetch the one list that is out of date rather than all of
them; it never says what they changed to, and a client answers by refetching.
That one property is what makes the rest of this module simple enough to trust:

* :meth:`Hub.publish` never awaits a socket, so a browser on a bad connection
  cannot slow down the transaction that is telling it something.
* A nudge already waiting is not queued again, because a second "the board
  changed" adds nothing to the first but what it was about — which is joined to
  the first's hint, so a burst is one nudge that names everything in it.
* A dropped event costs a refetch that the next event will trigger anyway.

The cost is one HTTP round trip per nudge. The thing it buys is that there is
exactly one piece of code that knows how to build a card — ``read_tasks()`` —
and no second, drifting copy of it here.

A third kind of event, ``agent.prompt`` (CYLIST-85 and CYLIST-89), rings
the board's doorbell like any change and also does two things no doorbell
does: it wakes a long-poll waiting for that prompt's answer, and for a
permission it puts the ``answer`` frame on the agent's own socket. See
:meth:`Hub._prompt_changed` for why that is still safe to drop.

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
from uuid import UUID, uuid4

from app.models.agent_session import SessionKey
from app.realtime import protocol
from app.realtime.events import AgentConnected, BoardChanged, Event, FeedChanged, PromptChanged

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
        waiting = self._pending.get(key)
        if waiting is not None:
            # A board doorbell already waiting has everything this one would
            # say, bar what it is about — so that is added to it, not dropped.
            if key == BOARD_CHANGED:
                self._pending[key] = protocol.merge_changed(waiting, nudge)
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
    """One agent session holding one socket.

    Keyed by the session's owner as well as its id (CYLIST-120): the id is the
    client's to choose, and keyed by it alone, anyone who knew another
    person's could connect under it, displace their socket, and be handed
    their permission answers.
    """

    key: SessionKey
    id: UUID = field(default_factory=uuid4)
    """This connection, as distinct from any other the same session makes —
    what tells a process that hears of a new socket whether it is its own."""
    stamped_at: float | None = None
    """When this socket last wrote ``socket_seen_at``, by the event loop's
    clock. ``None`` until it first has."""
    outbox: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    """Frames for the agent that are not replies — today only a permission's
    ``answer``. Drained by the socket's handler; see :meth:`Hub.deliver`."""
    displaced: asyncio.Event = field(default_factory=asyncio.Event)
    """Set when the same session connects again somewhere else — here or in
    another process. The handler that sees it closes without ending the
    database row, because the row now belongs to the newer socket."""


class Hub:
    """The connections this process is holding, and the way to nudge them."""

    def __init__(self) -> None:
        self._watchers: dict[UUID, set[Watcher]] = {}
        self._agents: dict[SessionKey, AgentLink] = {}
        self._prompt_waiters: dict[UUID, set[asyncio.Event]] = {}

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

    def publish(self, event: Event) -> None:
        """Nudge everyone here watching the project this event belongs to.

        Synchronous, and it never awaits: it is called from the code that has
        just committed a transaction, and that code is not waiting on anyone's
        network. An event for a project nobody is watching is dropped, which
        is the common case and costs a dictionary lookup.

        Called by :class:`~app.realtime.bus.Bus` for this process's own events
        and for every other process's, alike.
        """
        if isinstance(event, AgentConnected):
            self._agent_connected(event)
            return
        if isinstance(event, PromptChanged):
            self._prompt_changed(event)
        if not isinstance(event, BoardChanged | FeedChanged | PromptChanged):
            return
        if event.project_id is None:
            return
        watchers = self._watchers.get(event.project_id)
        if not watchers:
            return

        if isinstance(event, FeedChanged):
            key = f"{FeedChanged.type}:{event.task_id}"
            nudge = protocol.feed_changed(str(event.task_id))
        else:
            key = BOARD_CHANGED
            nudge = protocol.board_changed(
                protocol.changed_ref(event) if isinstance(event, BoardChanged) else None
            )
        for watcher in watchers:
            # A watcher with one waiting already has everything this would say.
            watcher.offer(key, nudge)

    def resync(self) -> None:
        """Ring every doorbell held here, and wake every long-poll.

        For when this process may have missed other processes' events — its
        line to them was down. Every message is a doorbell, so a missed one is
        made good by ringing again: each board refetches, each waiter re-reads
        its prompt. Costs one refetch per open board, once.
        """
        nudge = protocol.board_changed()
        for watchers in self._watchers.values():
            for watcher in watchers:
                watcher.offer(BOARD_CHANGED, nudge)
        for waiters in self._prompt_waiters.values():
            for waiter in waiters:
                waiter.set()

    # --- Prompts ------------------------------------------------------------

    @contextmanager
    def prompt_waiter(self, prompt_id: UUID) -> Iterator[asyncio.Event]:
        """An event set when this prompt next opens or closes, for as long as the block runs.

        What a long-poll for an ``ask_human`` answer waits on. Registered
        *before* the waiter reads the row, so a close committed between the
        read and the wait still wakes it.
        """
        waiter = asyncio.Event()
        self._prompt_waiters.setdefault(prompt_id, set()).add(waiter)
        try:
            yield waiter
        finally:
            waiters = self._prompt_waiters.get(prompt_id)
            if waiters is not None:
                waiters.discard(waiter)
                if not waiters:
                    del self._prompt_waiters[prompt_id]

    def _prompt_changed(self, event: PromptChanged) -> None:
        """Wake whoever waits on a prompt, and hand an agent its answer.

        The answer frame is the one thing published here that is not a
        doorbell, and it is still safe to lose: a daemon that never hears it
        re-sends the prompt on its next connection and is answered then, and
        a hook that is never answered leaves the terminal's dialog to decide.
        """
        for waiter in self._prompt_waiters.get(event.prompt_id, ()):
            waiter.set()
        if event.deliver is not None and event.person_id is not None and event.client_session_id:
            self.deliver(SessionKey(event.person_id, event.client_session_id), event.deliver)

    # --- Agents ------------------------------------------------------------

    def deliver(self, key: SessionKey, frame: dict[str, Any]) -> bool:
        """Queue a frame for an agent's socket. False if it holds none here —
        which, with several processes, is the usual answer: every process is
        told, and only the one holding the socket has anything to do."""
        link = self._agents.get(key)
        if link is None:
            return False
        link.outbox.put_nowait(frame)
        return True

    def register_agent(self, link: AgentLink) -> AgentLink | None:
        """Take ownership of a session, displacing any older socket held here.

        Last writer wins. The alternative — refusing the new socket — reads as
        safer and is worse: the socket being held may be a half-dead TCP
        connection that ping/pong will not give up on for another forty
        seconds, and the agent trying to reconnect is the one telling the
        truth about where it is.

        An older socket held by *another* process is displaced by the
        :class:`~app.realtime.events.AgentConnected` event the caller
        publishes next, which reaches that process's hub as
        :meth:`_agent_connected`.

        Returns the link that was displaced, if there was one, already marked
        so its handler knows to leave the database row alone.
        """
        previous = self._agents.get(link.key)
        if previous is not None:
            self._displace(previous)
        self._agents[link.key] = link
        return previous

    def release_agent(self, link: AgentLink) -> None:
        """Let go of a socket, unless a newer one has already taken over."""
        if self._agents.get(link.key) is link:
            del self._agents[link.key]

    def _agent_connected(self, event: AgentConnected) -> None:
        """A socket for some session opened, perhaps in another process.

        Whatever older socket this process holds for it is displaced — unless
        it is the very connection the event is about, which is how a process
        hears its own news back.
        """
        link = self._agents.get(SessionKey(event.person_id, event.client_session_id))
        if link is None or link.id == event.link_id:
            return
        del self._agents[link.key]
        self._displace(link)

    @staticmethod
    def _displace(link: AgentLink) -> None:
        link.displaced.set()
        logger.info("An agent session reconnected; displacing the socket it left")

    # --- Looking in --------------------------------------------------------

    def stats(self) -> dict[str, int]:
        """Counts, for ``/health/realtime``. No names, no content."""
        return {
            "agent_sockets": len(self._agents),
            "board_watchers": sum(len(group) for group in self._watchers.values()),
            "projects_watched": len(self._watchers),
        }
