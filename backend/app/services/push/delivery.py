"""Getting a queued agent-session push to the phones it is for.

:mod:`app.services.push.triggers` queues a :class:`SessionSnapshot` on the
outbox; once the transaction commits, the application's publisher hands it to
:meth:`Pusher.notify`, and :meth:`Pusher.run` — one task for the life of the
process — sends it, in the order the commits happened.

**Who is told** (:func:`recipients_of`): the person whose API token the agent
is reporting with, which is whoever runs it, and the card's owner, when that is
somebody else. The design asks for the card owner; the agent's own person is
added because a card handed to the agent person is owned by a machine with no
phone, and the person who started the agent is the one it is waiting for.

**Not durable**, unlike hook deliveries. A push is worth something for
minutes; a server restart that drops one in flight costs one stale
notification, and the session's next change sends a fresh one. So there is no
table and no retry, only a bounded in-memory queue (:data:`QUEUE_LIMIT`) that
drops the oldest when a stalled FCM lets it fill.

**Never on the request path.** The database is read in a short transaction of
its own and released before any network call, the way the hook courier does
it, so a slow FCM holds no connection and no row lock.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import Database
from app.models.api_token import ApiToken
from app.models.person import Person
from app.models.task import Task
from app.realtime.events import PushQueued
from app.services import devices
from app.services.push.fcm import PushSender, SendOutcome, redact
from app.services.push.messages import CardContext, SessionSnapshot, message_for

logger = logging.getLogger(__name__)

QUEUE_LIMIT = 1000
"""Pushes waiting to be sent before the oldest are dropped."""


@dataclass(frozen=True)
class Outgoing:
    """One message for one device, ready to send."""

    fcm_token: str
    message: dict[str, Any]


async def recipients_of(session: AsyncSession, snapshot: SessionSnapshot, task: Task) -> set[UUID]:
    """The people a change to this session is news to.

    The agent's own person and the card's owner, less the agent person —
    the machine has no phone, and the card being assigned to it is exactly
    the case where somebody else is the one to tell.
    """
    people: set[UUID] = {task.assignee_id}
    if snapshot.token_id is not None:
        owner = await session.scalar(
            select(ApiToken.person_id).where(ApiToken.id == snapshot.token_id)
        )
        if owner is not None:
            people.add(owner)
    agents = await session.scalars(
        select(Person.id).where(Person.id.in_(people), Person.is_agent.is_(True))
    )
    return people - set(agents)


async def outgoing_for(session: AsyncSession, snapshot: SessionSnapshot) -> list[Outgoing]:
    """Every message one snapshot becomes: one per device of each recipient."""
    task = await session.get(Task, snapshot.task_id)
    if task is None:
        return []  # the card was deleted since; nothing to point a phone at
    card = CardContext(reference=task.reference, title=task.title, project_key=task.project.key)
    people = await recipients_of(session, snapshot, task)
    return [
        Outgoing(device.fcm_token, message_for(device.fcm_token, snapshot, card))
        for device in await devices.recipients(session, people)
    ]


async def deliver(database: Database, sender: PushSender, snapshot: SessionSnapshot) -> int:
    """Send one snapshot to everyone it is for. Returns how many were sent.

    Devices FCM reports as gone are forgotten afterwards, in a transaction of
    their own.
    """
    async with database.session() as session:
        outgoing = await outgoing_for(session, snapshot)
    if not outgoing:
        return 0

    sent = 0
    gone: list[str] = []
    for item in outgoing:
        outcome = await sender.send(item.message)
        if outcome is SendOutcome.SENT:
            sent += 1
        elif outcome is SendOutcome.UNREGISTERED:
            gone.append(item.fcm_token)

    if gone:
        async with database.session() as session:
            await devices.forget_tokens(session, gone)
    if sender.enabled:
        logger.info(
            "Pushed an agent session's state",
            extra={
                "context": {
                    "state": snapshot.state.value,
                    "reason": snapshot.reason.value if snapshot.reason else None,
                    "devices": len(outgoing),
                    "sent": sent,
                    "forgotten": [redact(token) for token in gone],
                }
            },
        )
    return sent


class Pusher:
    """Sends committed agent-session pushes, one at a time, in commit order.

    Exists from the moment the app is built, so the publisher can hand it
    events; :meth:`run` starts in the lifespan.
    """

    def __init__(self) -> None:
        self._queue: asyncio.Queue[SessionSnapshot] = asyncio.Queue(maxsize=QUEUE_LIMIT)

    def notify(self, event: PushQueued) -> None:
        """Take one committed outbox event. Never blocks and never raises."""
        snapshot = event.snapshot
        if not isinstance(snapshot, SessionSnapshot):  # pragma: no cover - defensive
            return
        if self._queue.full():
            self._queue.get_nowait()
            logger.warning("The push queue is full; dropped the oldest push")
        self._queue.put_nowait(snapshot)

    def pending(self) -> int:
        return self._queue.qsize()

    async def run(self, database: Database, sender: PushSender) -> None:
        """Send whatever is queued, forever. One bad push never stops it."""
        while True:
            snapshot = await self._queue.get()
            try:
                await deliver(database, sender, snapshot)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Sending an agent-session push failed")
            finally:
                self._queue.task_done()

    async def drain(self, database: Database, sender: PushSender) -> int:
        """Send everything queued now and return how many messages went. For tests."""
        sent = 0
        while not self._queue.empty():
            sent += await deliver(database, sender, self._queue.get_nowait())
            self._queue.task_done()
        return sent
