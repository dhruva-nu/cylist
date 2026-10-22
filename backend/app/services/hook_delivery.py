"""Getting a queued event to a hook's URL, and trying again when it does not arrive.

:func:`app.services.hooks.queue_for` writes a ``hook_delivery`` row in the
transaction that made the change; everything after the commit happens here.
The :class:`Courier` runs for the life of the process, woken by the outbox the
moment a transaction that queued something commits, and on a timer otherwise
so a retry that has come due is not left waiting for the next change.

**Each delivery goes in three steps** — claim, send, record — the first and
last each in its own short transaction, so no row lock is held across somebody
else's network. Claiming pushes ``next_attempt_at`` out by :data:`LEASE`, so a
send that dies leaves its row to be taken again once the lease runs out rather
than stuck. ``FOR UPDATE SKIP LOCKED`` keeps two passes — two workers, should
there ever be more than one — from taking the same row.

**One receiver cannot hold up the rest.** Every send has :data:`TOTAL_TIMEOUT`
in all, however slowly the bytes trickle in, and no more than
:data:`RESPONSE_MAX_BYTES` of an answer is read. The courier keeps up to
:data:`CONCURRENCY` sends in flight and records each the moment it lands,
rather than waiting for a batch to finish; a pass takes at most
:data:`PER_HOOK` rows of any one hook, and none of a hook that still has sends
in flight, so a queue of hundreds for one dead receiver cannot fill every
slot. Anything a send raises — a URL the client will not build, a bug — is
written down as a failed attempt, never left to abort the pass and send the
rest again.

**A receiver that keeps failing is left alone for a while.** After
:data:`BREAKER_THRESHOLD` failed attempts in a row its hook's breaker opens:
nothing is sent to it until :attr:`~app.models.hook.Hook.paused_until`, and
waiting spends none of a delivery's attempts. Then a single delivery goes as a
probe; a 2xx closes the breaker, a failure opens it again for twice as long,
up to :data:`BREAKER_MAX_PAUSE`. Opening it, and a delivery giving up for
good, are both logged at WARNING, so an operator can see a hook go quiet.

**What counts as delivered** is a 2xx, and nothing else: a redirect is not
followed, because a signed body re-sent somewhere the hook's owner did not
name is not delivery. Anything else is tried again after the next step of
:data:`BACKOFF`, up to :data:`MAX_ATTEMPTS` in all — about nine hours of a
receiver being down — and then the row is ``failed`` and stays so until
someone sends it again by hand.

**Every request is signed**, Stripe-style, so a receiver can tell it came from
this deployment and was not replayed from last week:

    X-Cylist-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256(secret, "<t>." + body)>

:func:`verify` is the receiving half, written out so the two can be tested
against each other and so a receiver in Python has something to copy.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import random
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import and_, case, delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now
from app.core.crypto import VaultKeys, cipher_for
from app.core.metrics import live
from app.db import Database
from app.models.hook import DeliveryState, Hook, HookDelivery
from app.services import hooks, retention

logger = logging.getLogger(__name__)

_chance = random.SystemRandom()
"""Draws :data:`JITTER`. The operating system's source costs nothing at this
rate, and spares a reader wondering whether a guessable one would matter."""

BACKOFF = (
    timedelta(seconds=30),
    timedelta(minutes=2),
    timedelta(minutes=10),
    timedelta(minutes=30),
    timedelta(hours=2),
    timedelta(hours=6),
)
"""How long to wait after each failed attempt before the next."""

MAX_ATTEMPTS = len(BACKOFF) + 1

JITTER = 0.2
"""How far either way of its step of :data:`BACKOFF` a retry may land, as a
fraction of it — so the deliveries a receiver's outage refused do not all
come back at the same second when it recovers."""

TIMEOUT = timedelta(seconds=10)
"""How long the client waits on any one step — connecting, writing, each read.
A hook is a notification, not an RPC: anything slow belongs behind the
receiver's own queue."""

TOTAL_TIMEOUT = timedelta(seconds=20)
"""How long a whole send may take. :data:`TIMEOUT` alone is per step, so a
receiver answering a byte every nine seconds would never trip it."""

RESPONSE_MAX_BYTES = 64 * 1024
"""How much of an answer is read. Only a refusal's start is kept, and a
receiver that answers with a gigabyte should cost a gigabyte of nobody's
memory."""

LEASE = timedelta(minutes=2)
"""How long a claimed row is left alone. Comfortably longer than
:data:`TOTAL_TIMEOUT`, so a slow send is not claimed again while in flight."""

BATCH = 20
"""Rows taken by one call to :func:`deliver_due`."""

CONCURRENCY = 20
"""Sends the courier keeps in flight at once."""

PER_HOOK = 4
"""Rows of any one hook taken by one pass, so a backlog on one hook leaves
room for everybody else's."""

BREAKER_THRESHOLD = 5
"""Failed attempts in a row, across a hook's deliveries, that open its breaker."""

BREAKER_PAUSE = timedelta(minutes=1)
"""How long the breaker stays open the first time. Each failed probe after
that doubles it."""

BREAKER_MAX_PAUSE = timedelta(hours=1)
"""The longest the breaker stays open at once, so a receiver that comes back
is noticed within the hour."""

WAKE_EVERY = timedelta(seconds=15)
"""How often the courier looks without being woken — the longest a due retry
waits past its time."""

KEEP_FOR = timedelta(days=30)
"""How long a finished delivery stays in the log."""

PRUNE_EVERY = timedelta(hours=1)

ERROR_MAX_LENGTH = 500
"""How much of a refusal is kept: enough to read the receiver's complaint, not
enough to fill the table with somebody's error page."""

SIGNATURE_HEADER = "X-Cylist-Signature"
SIGNATURE_TOLERANCE = timedelta(minutes=5)

TEST_EVENT = "hook.test"
FIRED_EVENT = "hook.fired"


# --- Signing ---------------------------------------------------------------


def sign(secret: str, timestamp: int, body: bytes) -> str:
    """The value of :data:`SIGNATURE_HEADER` for one body at one moment."""
    mac = hmac.new(secret.encode(), f"{timestamp}.".encode() + body, hashlib.sha256)
    return f"t={timestamp},v1={mac.hexdigest()}"


def verify(secret: str, header: str, body: bytes, *, at: float | None = None) -> bool:
    """Whether a received request was signed with this secret, recently.

    The receiving half of :func:`sign`. Refuses a signature older than
    :data:`SIGNATURE_TOLERANCE`, so a captured request cannot be replayed
    later, and compares in constant time.
    """
    parts = dict(part.split("=", 1) for part in header.split(",") if "=" in part)
    try:
        timestamp = int(parts["t"])
    except (KeyError, ValueError):
        return False
    current = time.time() if at is None else at
    if abs(current - timestamp) > SIGNATURE_TOLERANCE.total_seconds():
        return False
    expected = sign(secret, timestamp, body).split("v1=", 1)[1]
    return hmac.compare_digest(expected, parts.get("v1", ""))


def encode(payload: dict[str, Any]) -> bytes:
    """The bytes sent, and so the bytes signed. Compact, so the signature is
    over exactly what arrives rather than over one of its pretty-printings."""
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()


# --- One attempt -----------------------------------------------------------


@dataclass(frozen=True)
class _Parcel:
    """What a send needs, copied off the rows so it can outlive their session."""

    delivery_id: UUID
    hook_id: UUID
    event: str
    url: str
    secret: str
    payload: dict[str, Any]

    @classmethod
    def of(cls, delivery: HookDelivery, hook: Hook, secret: str) -> _Parcel:
        return cls(delivery.id, hook.id, delivery.event, hook.url, secret, delivery.payload)


@dataclass(frozen=True)
class Attempt:
    at: datetime
    status_code: int | None
    error: str | None
    duration_ms: int

    @property
    def delivered(self) -> bool:
        return self.status_code is not None and 200 <= self.status_code < 300


async def _send(client: httpx.AsyncClient, parcel: _Parcel) -> Attempt:
    """One attempt, whatever happens to it. Never raises: a send that cannot be
    made is an attempt that failed, and the delivery's log says why."""
    started = time.monotonic()
    at = now()
    try:
        async with asyncio.timeout(TOTAL_TIMEOUT.total_seconds()):
            status_code, said = await _post(client, parcel, at)
    except TimeoutError:
        late = f"TimeoutError: no answer within {TOTAL_TIMEOUT.total_seconds():g} seconds"
        return Attempt(at=at, status_code=None, error=late, duration_ms=_since(started))
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        return Attempt(at=at, status_code=None, error=_described(exc), duration_ms=_since(started))
    except Exception as exc:
        # Not the network's doing, so worth a traceback — but still only this
        # delivery's problem, and recorded as such.
        logger.exception("Sending hook delivery %s failed unexpectedly", parcel.delivery_id)
        return Attempt(at=at, status_code=None, error=_described(exc), duration_ms=_since(started))

    error: str | None = None
    if not 200 <= status_code < 300:
        error = f"HTTP {status_code}"
        said = " ".join(said.split())
        if said:
            error = _clipped(f"{error}: {said}")
    return Attempt(at=at, status_code=status_code, error=error, duration_ms=_since(started))


async def _post(client: httpx.AsyncClient, parcel: _Parcel, at: datetime) -> tuple[int, str]:
    """The request itself: the answer's status and the start of its body."""
    body = encode(parcel.payload)
    async with client.stream(
        "POST",
        parcel.url,
        content=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "Cylist-Hooks/1",
            # Raw bytes are what is capped, so none that inflate once read.
            "Accept-Encoding": "identity",
            "X-Cylist-Event": parcel.event,
            "X-Cylist-Delivery": str(parcel.delivery_id),
            SIGNATURE_HEADER: sign(parcel.secret, int(at.timestamp()), body),
        },
        timeout=TIMEOUT.total_seconds(),
        follow_redirects=False,
    ) as response:
        if response.is_stream_consumed:
            # A transport that read the answer itself before handing it over —
            # httpx's own MockTransport does — has left nothing to stream.
            return response.status_code, response.content[:RESPONSE_MAX_BYTES].decode(
                errors="replace"
            )
        read = bytearray()
        async for chunk in response.aiter_raw():
            read += chunk[: RESPONSE_MAX_BYTES - len(read)]
            if len(read) >= RESPONSE_MAX_BYTES:
                # Leaving the block closes the connection on the rest.
                break
        return response.status_code, read.decode(errors="replace")


def _described(exc: BaseException) -> str:
    return _clipped(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)


def _clipped(error: str) -> str:
    """Keep as much of a refusal as :data:`ERROR_MAX_LENGTH` allows."""
    return error[:ERROR_MAX_LENGTH]


def _since(started: float) -> int:
    return round((time.monotonic() - started) * 1000)


def record(delivery: HookDelivery, attempt: Attempt) -> None:
    """Write one attempt's outcome onto its delivery, and decide what comes next."""
    delivery.attempt_count += 1
    delivery.attempts = [
        *delivery.attempts,
        {
            "at": attempt.at.isoformat(),
            "status_code": attempt.status_code,
            "error": attempt.error,
            "duration_ms": attempt.duration_ms,
        },
    ]
    delivery.last_status_code = attempt.status_code
    delivery.last_error = attempt.error
    if attempt.delivered:
        delivery.state = DeliveryState.DELIVERED
        delivery.delivered_at = attempt.at
        delivery.next_attempt_at = None
    elif delivery.event == TEST_EVENT or delivery.attempt_count >= MAX_ATTEMPTS:
        # A test is answered on the page that sent it; trying it again in the
        # background would only fill the log with the same answer.
        delivery.state = DeliveryState.FAILED
        delivery.next_attempt_at = None
        live().deliveries_failed += 1
        logger.warning(
            "Hook %s gave up on delivery %s of %s after attempt %d: %s",
            delivery.hook_id,
            delivery.id,
            delivery.event,
            delivery.attempt_count,
            attempt.error,
        )
    else:
        delivery.state = DeliveryState.PENDING
        step = min(delivery.attempt_count, len(BACKOFF)) - 1
        delivery.next_attempt_at = attempt.at + BACKOFF[step] * _chance.uniform(
            1 - JITTER, 1 + JITTER
        )


async def send_now(
    session: AsyncSession,
    client: httpx.AsyncClient,
    vault_keys: VaultKeys,
    hook: Hook,
    delivery: HookDelivery,
) -> HookDelivery:
    """Send one delivery inside the caller's request, and record the answer.

    For "Send test" and for firing a manual hook, whose page is waiting for the
    answer, which a courier pass could only give it later. A firing the
    receiver refuses is left pending, for the courier to try again.
    """
    secret = hooks.secret_of(cipher_for(vault_keys), hook)
    await session.flush()
    attempt = await _send(client, _Parcel.of(delivery, hook, secret))
    record(delivery, attempt)
    return delivery


def requeue(delivery: HookDelivery) -> None:
    """Send a delivery again at the courier's next pass, whatever became of it.

    One more attempt, not a fresh set: a delivery that had already failed
    :data:`MAX_ATTEMPTS` times fails again at once if this one is refused too,
    rather than spending another nine hours on a receiver still not there.
    Nor does it reach past the hook's breaker: while that is open the delivery
    waits its turn with the others, as it would have had it never failed.
    """
    delivery.state = DeliveryState.PENDING
    delivery.next_attempt_at = now()


# --- The courier -----------------------------------------------------------


async def deliver_due(
    database: Database,
    client: httpx.AsyncClient,
    vault_keys: VaultKeys,
    *,
    at: datetime | None = None,
) -> int:
    """One pass: send what is due, up to :data:`BATCH` rows and :data:`PER_HOOK`
    of any one hook, and wait for every answer. Returns how many were sent,
    delivered or not.

    The courier does not use this — it keeps its sends in flight rather than
    waiting on the slowest — but a script or a test that wants a pass to have
    finished when it returns does.
    """
    parcels = await _claim(database, vault_keys, at or now(), limit=BATCH)
    outcomes = await asyncio.gather(
        *(_deliver(database, client, parcel) for parcel in parcels), return_exceptions=True
    )
    for parcel, outcome in zip(parcels, outcomes, strict=True):
        if isinstance(outcome, BaseException):
            logger.error("Recording hook delivery %s failed", parcel.delivery_id, exc_info=outcome)
    return len(parcels)


async def _deliver(database: Database, client: httpx.AsyncClient, parcel: _Parcel) -> None:
    """Send one claimed delivery and record the answer, in a transaction of its
    own, the moment it comes — not when the rest of its pass is done."""
    attempt = await _send(client, parcel)
    async with database.session() as session:
        delivery = await session.get(HookDelivery, parcel.delivery_id)
        # Gone means its hook was deleted while the request was in flight.
        if delivery is None:
            return
        record(delivery, attempt)
        await _count(session, parcel.hook_id, attempt)


async def _count(session: AsyncSession, hook_id: UUID, attempt: Attempt) -> None:
    """Move a hook's breaker on one attempt's outcome.

    The count is bumped in the statement rather than read and written back, so
    two sends to the same hook landing at once both count. ``updated_at`` is
    held where it was: the hook's rule has not been edited, and the page that
    says when it last was should not say otherwise.
    """
    unedited = {"updated_at": Hook.updated_at}
    if attempt.delivered:
        await session.execute(
            update(Hook)
            .where(Hook.id == hook_id, Hook.consecutive_failures > 0)
            .values(consecutive_failures=0, paused_until=None, **unedited)
        )
        return
    failures = await session.scalar(
        update(Hook)
        .where(Hook.id == hook_id)
        .values(consecutive_failures=Hook.consecutive_failures + 1, **unedited)
        .returning(Hook.consecutive_failures)
    )
    if failures is None or failures < BREAKER_THRESHOLD:
        return
    # The doubling stops once it has passed the cap, before it can overflow.
    doublings = min(failures - BREAKER_THRESHOLD, 16)
    pause = min(BREAKER_PAUSE * 2**doublings, BREAKER_MAX_PAUSE)
    await session.execute(
        update(Hook).where(Hook.id == hook_id).values(paused_until=attempt.at + pause, **unedited)
    )
    logger.warning(
        "Hook %s has failed %d attempts in a row; sending nothing more to it for %s: %s",
        hook_id,
        failures,
        pause,
        attempt.error,
    )


async def _claim(
    database: Database,
    vault_keys: VaultKeys,
    moment: datetime,
    *,
    limit: int,
    busy: frozenset[UUID] = frozenset(),
) -> list[_Parcel]:
    """Lease up to ``limit`` due rows: at most :data:`PER_HOOK` of a hook, one of
    a hook whose breaker is waiting on a probe, none of a hook whose breaker is
    open or that is in ``busy`` — still has sends in flight."""
    if limit <= 0:
        return []
    due = and_(
        HookDelivery.state == DeliveryState.PENDING,
        HookDelivery.next_attempt_at <= moment,
    )
    reachable = or_(Hook.paused_until.is_(None), Hook.paused_until <= moment)
    ranked = (
        select(
            HookDelivery.id,
            Hook.consecutive_failures,
            func.row_number()
            .over(
                partition_by=HookDelivery.hook_id,
                order_by=(HookDelivery.next_attempt_at, HookDelivery.id),
            )
            .label("place"),
        )
        .join(Hook, Hook.id == HookDelivery.hook_id)
        .where(due, reachable, Hook.id.not_in(busy))
        .subquery()
    )
    allowed = select(ranked.c.id).where(
        ranked.c.place
        <= case((ranked.c.consecutive_failures >= BREAKER_THRESHOLD, 1), else_=PER_HOOK)
    )
    async with database.session() as session:
        # Ranked first and locked after: Postgres cannot lock the rows of a
        # windowed query, and a row another pass took in between is skipped.
        rows = (
            await session.execute(
                select(HookDelivery, Hook)
                .join(Hook, Hook.id == HookDelivery.hook_id)
                .where(HookDelivery.id.in_(allowed), due)
                .order_by(HookDelivery.next_attempt_at)
                .limit(limit)
                .with_for_update(of=HookDelivery, skip_locked=True)
            )
        ).all()
        if not rows:
            return []
        cipher = cipher_for(vault_keys)
        parcels = []
        for delivery, hook in rows:
            delivery.next_attempt_at = moment + LEASE
            parcels.append(_Parcel.of(delivery, hook, hooks.secret_of(cipher, hook)))
        return parcels


async def prune(database: Database, *, at: datetime | None = None) -> None:
    """Forget finished deliveries older than :data:`KEEP_FOR`."""
    async with database.session() as session:
        await session.execute(
            delete(HookDelivery).where(
                HookDelivery.state != DeliveryState.PENDING,
                HookDelivery.created_at < (at or now()) - KEEP_FOR,
            )
        )


class Courier:
    """Sends due deliveries for as long as the application runs."""

    def __init__(self, keep: retention.Retention | None = None) -> None:
        self._wake = asyncio.Event()
        self._keep = keep
        """What the hourly prune also trims beyond this module's own log — see
        :mod:`app.services.retention`. ``None`` trims nothing else, which is
        what a test that only wants deliveries sent asks for."""

    def wake(self) -> None:
        """Look now rather than at the next tick. Called by the outbox, after a
        transaction that queued deliveries has committed."""
        self._wake.set()

    async def run(
        self,
        database: Database,
        vault_keys: VaultKeys,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Never returns until cancelled, and never dies of one bad pass — the
        same promise the agent-session reaper makes, for the same reason.

        Up to :data:`CONCURRENCY` sends are in flight at once, each recording
        its own answer and waking the loop as it lands, so a free slot is
        filled at once rather than when the slowest of a batch is done.

        ``transport`` is the network unless a test answers in its place.
        """
        last_pruned: datetime | None = None
        in_flight: dict[asyncio.Task[None], UUID] = {}

        def landed(task: asyncio.Task[None]) -> None:
            in_flight.pop(task, None)
            if not task.cancelled() and task.exception() is not None:
                logger.error("A hook delivery failed", exc_info=task.exception())
            self._wake.set()

        async with httpx.AsyncClient(transport=transport) as client:
            try:
                while True:
                    # Cleared before the pass, so a wake that arrives during it
                    # earns another pass rather than being swallowed.
                    self._wake.clear()
                    try:
                        parcels = await _claim(
                            database,
                            vault_keys,
                            now(),
                            limit=CONCURRENCY - len(in_flight),
                            busy=frozenset(in_flight.values()),
                        )
                        for parcel in parcels:
                            task = asyncio.create_task(_deliver(database, client, parcel))
                            in_flight[task] = parcel.hook_id
                            task.add_done_callback(landed)
                        if last_pruned is None or now() - last_pruned > PRUNE_EVERY:
                            await prune(database)
                            last_pruned = now()
                            if self._keep is not None:
                                await retention.apply(database, self._keep)
                    except asyncio.CancelledError:
                        raise
                    except Exception:
                        logger.exception("A hook delivery pass failed")
                    with suppress(TimeoutError):
                        await asyncio.wait_for(self._wake.wait(), WAKE_EVERY.total_seconds())
            finally:
                # Whatever was in flight keeps its lease and is sent again once
                # that runs out.
                for task in list(in_flight):
                    task.cancel()
                await asyncio.gather(*in_flight, return_exceptions=True)
