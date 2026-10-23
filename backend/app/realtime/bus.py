"""Every process's news, to every process: Postgres ``LISTEN``/``NOTIFY``.

A board socket, an agent socket and a long-poll are each held by one process,
and the change they are waiting to hear about may be committed by any other
(CYLIST-133). :class:`Bus` is the join. A committed event is handed to this
process's :class:`~app.realtime.hub.Hub` straight away, as it always was, and
also sent as a ``NOTIFY`` on :data:`CHANNEL`; every process ``LISTEN``\\ s on a
connection of its own and hands what it hears to its own hub.

Postgres rather than anything new, because it is already the one thing every
process shares, and a deployment that needed Redis to run two workers would
have swapped a constraint for a dependency.

Nothing about this is more reliable than a doorbell needs to be, and that is
the point of everything published being one — see :mod:`app.realtime.hub`:

* An event is told locally first, so one process on its own behaves exactly
  as it did before there was a bus, whether or not the bus is up.
* The line can drop. Events sent while it is down are lost, and so are other
  processes' events this one would have heard; on reconnecting, every board
  and long-poll held here is rung once (:meth:`Hub.resync`), which is what
  any missed doorbell would have made them do.
* A permission's answer is the one event that carries something, and is
  still safe to lose: the daemon re-sends an unanswered prompt when it next
  connects, and is told the outcome then.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from uuid import uuid4

import asyncpg
from asyncpg.pool import PoolConnectionProxy
from sqlalchemy.engine import make_url

from app.realtime.events import BoardChanged, Event, decode
from app.realtime.hub import Hub

logger = logging.getLogger(__name__)

CHANNEL = "cylist_events"

_ORIGIN = "origin"

_PAYLOAD_MAX = 7900
"""A little under Postgres's 8000-byte ceiling on a notification. Events are a
handful of ids; one that is not is sent as a bare doorbell instead."""

_BACKLOG = 1000
"""Events waiting to be sent before the oldest are dropped. Only reached while
the line is down, and a doorbell from minutes ago is worth nothing."""

RETRY_AFTER = 2.0
"""Seconds between attempts to reconnect the line."""


class Bus:
    """Hands each committed event to this process's hub and to every other's."""

    def __init__(self, hub: Hub) -> None:
        self._hub = hub
        self._origin = str(uuid4())
        """This process, as its notifications say: what it skips when it hears
        its own news back, having already told its own hub."""
        self._outgoing: asyncio.Queue[str] | None = None
        """``None`` until :meth:`run` starts — a bus nobody runs, as in a test
        built without the lifespan, is this process's hub and nothing more."""
        self.listening = asyncio.Event()
        """Set while the line is up and other processes' news is arriving."""

    def publish(self, event: Event) -> None:
        """Tell this process now, and every other once the line carries it.

        Synchronous and never awaits, like :meth:`Hub.publish`: it is called
        just after a commit, and the request is not waiting on the network.
        An event that is for this process alone (``wire = False``) goes no
        further than its hub.
        """
        self._hub.publish(event)
        if self._outgoing is None or not event.wire:
            return
        if self._outgoing.full():
            self._outgoing.get_nowait()
        self._outgoing.put_nowait(self._encode(event))

    async def run(self, database_url: str) -> None:
        """Hold the line for the life of the process, reconnecting as needed."""
        outgoing: asyncio.Queue[str] = asyncio.Queue(maxsize=_BACKLOG)
        self._outgoing = outgoing
        dsn = (
            make_url(database_url)
            .set(drivername="postgresql")
            .render_as_string(hide_password=False)
        )
        first = True
        while True:
            try:
                await self._serve(dsn, outgoing, resync=not first)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("The event bus lost its connection; reconnecting")
            first = False
            await asyncio.sleep(RETRY_AFTER)

    async def _serve(self, dsn: str, outgoing: asyncio.Queue[str], *, resync: bool) -> None:
        """Listen and send on one connection until it closes."""
        connection = await _connect(dsn)
        closed = asyncio.Event()
        connection.add_termination_listener(lambda _: closed.set())
        try:
            await connection.add_listener(CHANNEL, self._heard)
            self.listening.set()
            if resync:
                # Whatever the others said while the line was down is lost.
                self._hub.resync()
            await _send_until(connection, outgoing, closed)
        finally:
            self.listening.clear()
            if not connection.is_closed():
                await connection.close()

    def _heard(
        self,
        _connection: asyncpg.Connection[Any] | PoolConnectionProxy[Any],
        _pid: int,
        _channel: str,
        payload: object,
    ) -> None:
        """One notification: another process's event, for this one's hub."""
        if not isinstance(payload, str):
            return
        try:
            decoded = json.loads(payload)
        except ValueError:
            logger.warning("Ignored a notification on %s that is not JSON", CHANNEL)
            return
        if not isinstance(decoded, dict) or decoded.pop(_ORIGIN, None) == self._origin:
            return
        event = decode(decoded)
        if event is None:
            logger.warning("Ignored an event of a kind this process cannot read")
            return
        self._hub.publish(event)

    def _encode(self, event: Event) -> str:
        """An event as a notification, or its bare doorbell if it is too big."""
        payload = json.dumps({**event.to_wire(), _ORIGIN: self._origin}, default=str)
        if len(payload.encode()) <= _PAYLOAD_MAX:
            return payload
        doorbell = BoardChanged(project_id=getattr(event, "project_id", None))
        return json.dumps({**doorbell.to_wire(), _ORIGIN: self._origin}, default=str)


async def _connect(dsn: str) -> asyncpg.Connection[Any]:
    """Open the line, and leave nothing open if whoever is waiting for it is cancelled.

    A connection that finishes opening just as its owner is cancelled — the
    process is shutting down, or a test built an app and tore it down again —
    has no owner left to close it, and is closed only when it is garbage
    collected, with a warning. So the connect runs on its own, and if it
    outlives the wait it is torn down the moment it completes.
    """
    opening = asyncio.ensure_future(asyncpg.connect(dsn))
    try:
        return await asyncio.shield(opening)
    except asyncio.CancelledError:
        opening.add_done_callback(_terminate_if_open)
        opening.cancel()
        raise


def _terminate_if_open(opening: asyncio.Future[asyncpg.Connection[Any]]) -> None:
    if not opening.cancelled() and opening.exception() is None:
        opening.result().terminate()


async def _send_until(
    connection: asyncpg.Connection[Any], outgoing: asyncio.Queue[str], closed: asyncio.Event
) -> None:
    """Send queued events until the connection goes away."""
    ended = asyncio.ensure_future(closed.wait())
    try:
        while True:
            queued = asyncio.ensure_future(outgoing.get())
            done, _ = await asyncio.wait({queued, ended}, return_when=asyncio.FIRST_COMPLETED)
            if queued not in done:
                queued.cancel()
                return
            await connection.execute("SELECT pg_notify($1, $2)", CHANNEL, queued.result())
    finally:
        ended.cancel()
