"""Getting a queued event to a hook's URL, and trying again when it does not arrive.

:func:`app.services.hooks.queue_for` writes a ``hook_delivery`` row in the
transaction that made the change; everything after the commit happens here.
The :class:`Courier` runs for the life of the process, woken by the outbox the
moment a transaction that queued something commits, and on a timer otherwise
so a retry that has come due is not left waiting for the next change.

**Each pass takes its rows in three steps** — claim, send, record — each in its
own short transaction, so no row lock is held across somebody else's network.
Claiming pushes ``next_attempt_at`` out by :data:`LEASE`, so a pass that dies
mid-send leaves its rows to be taken again once the lease runs out rather than
stuck. ``FOR UPDATE SKIP LOCKED`` keeps two passes — two workers, should there
ever be more than one — from taking the same row.

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
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now
from app.core.crypto import cipher_for
from app.db import Database
from app.models.hook import DeliveryState, Hook, HookDelivery
from app.services import hooks

logger = logging.getLogger(__name__)

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

TIMEOUT = timedelta(seconds=10)
"""How long a receiver has to answer. A hook is a notification, not an RPC:
anything slow belongs behind the receiver's own queue."""

LEASE = timedelta(minutes=2)
"""How long a claimed row is left alone. Comfortably longer than
:data:`TIMEOUT`, so a slow send is not claimed again while it is in flight."""

BATCH = 20
"""Rows taken per pass. Sent concurrently, so one slow receiver holds up a
pass by :data:`TIMEOUT` at most, not by its share of the queue."""

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
    event: str
    url: str
    secret: str
    payload: dict[str, Any]


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
    body = encode(parcel.payload)
    started = time.monotonic()
    at = now()
    try:
        response = await client.post(
            parcel.url,
            content=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "Cylist-Hooks/1",
                "X-Cylist-Event": parcel.event,
                "X-Cylist-Delivery": str(parcel.delivery_id),
                SIGNATURE_HEADER: sign(parcel.secret, int(at.timestamp()), body),
            },
            timeout=TIMEOUT.total_seconds(),
            follow_redirects=False,
        )
    except httpx.HTTPError as exc:
        return Attempt(
            at=at,
            status_code=None,
            error=(f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__)[
                :ERROR_MAX_LENGTH
            ],
            duration_ms=_since(started),
        )
    error = None
    if not 200 <= response.status_code < 300:
        said = " ".join(response.text.split())
        error = f"HTTP {response.status_code}" + (f": {said}" if said else "")
    return Attempt(
        at=at,
        status_code=response.status_code,
        error=error[:ERROR_MAX_LENGTH] if error else None,
        duration_ms=_since(started),
    )


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
    else:
        delivery.state = DeliveryState.PENDING
        step = min(delivery.attempt_count, len(BACKOFF)) - 1
        delivery.next_attempt_at = attempt.at + BACKOFF[step]


async def send_now(
    session: AsyncSession,
    client: httpx.AsyncClient,
    vault_key: str,
    hook: Hook,
    delivery: HookDelivery,
) -> HookDelivery:
    """Send one delivery inside the caller's request, and record the answer.

    For "Send test", whose whole point is the answer: the page that asked is
    waiting for it, which a courier pass could only give it later.
    """
    secret = hooks.secret_of(cipher_for(vault_key), hook)
    await session.flush()
    attempt = await _send(
        client,
        _Parcel(delivery.id, delivery.event, hook.url, secret, delivery.payload),
    )
    record(delivery, attempt)
    return delivery


def requeue(delivery: HookDelivery) -> None:
    """Send a delivery again at the courier's next pass, whatever became of it.

    One more attempt, not a fresh set: a delivery that had already failed
    :data:`MAX_ATTEMPTS` times fails again at once if this one is refused too,
    rather than spending another nine hours on a receiver still not there.
    """
    delivery.state = DeliveryState.PENDING
    delivery.next_attempt_at = now()


# --- The courier -----------------------------------------------------------


async def deliver_due(
    database: Database, client: httpx.AsyncClient, vault_key: str, *, at: datetime | None = None
) -> int:
    """One pass: send every delivery that is due, up to :data:`BATCH`. Returns
    how many were sent, delivered or not."""
    moment = at or now()
    parcels = await _claim(database, vault_key, moment)
    if not parcels:
        return 0
    attempts = await asyncio.gather(*(_send(client, parcel) for parcel in parcels))
    async with database.session() as session:
        for parcel, attempt in zip(parcels, attempts, strict=True):
            delivery = await session.get(HookDelivery, parcel.delivery_id)
            # Gone means its hook was deleted while the request was in flight.
            if delivery is not None:
                record(delivery, attempt)
    return len(parcels)


async def _claim(database: Database, vault_key: str, moment: datetime) -> list[_Parcel]:
    async with database.session() as session:
        rows = (
            await session.execute(
                select(HookDelivery, Hook)
                .join(Hook, Hook.id == HookDelivery.hook_id)
                .where(
                    HookDelivery.state == DeliveryState.PENDING,
                    HookDelivery.next_attempt_at <= moment,
                )
                .order_by(HookDelivery.next_attempt_at)
                .limit(BATCH)
                .with_for_update(of=HookDelivery, skip_locked=True)
            )
        ).all()
        if not rows:
            return []
        cipher = cipher_for(vault_key)
        parcels = []
        for delivery, hook in rows:
            delivery.next_attempt_at = moment + LEASE
            parcels.append(
                _Parcel(
                    delivery.id,
                    delivery.event,
                    hook.url,
                    hooks.secret_of(cipher, hook),
                    delivery.payload,
                )
            )
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

    def __init__(self) -> None:
        self._wake = asyncio.Event()

    def wake(self) -> None:
        """Look now rather than at the next tick. Called by the outbox, after a
        transaction that queued deliveries has committed."""
        self._wake.set()

    async def run(
        self,
        database: Database,
        vault_key: str,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """Never returns until cancelled, and never dies of one bad pass — the
        same promise the agent-session reaper makes, for the same reason.

        ``transport`` is the network unless a test answers in its place.
        """
        last_pruned: datetime | None = None
        async with httpx.AsyncClient(transport=transport) as client:
            while True:
                # Cleared before the pass, so a wake that arrives during it
                # earns another pass rather than being swallowed.
                self._wake.clear()
                try:
                    while await deliver_due(database, client, vault_key) == BATCH:
                        pass
                    if last_pruned is None or now() - last_pruned > PRUNE_EVERY:
                        await prune(database)
                        last_pruned = now()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("A hook delivery pass failed")
                with suppress(TimeoutError):
                    await asyncio.wait_for(self._wake.wait(), WAKE_EVERY.total_seconds())
