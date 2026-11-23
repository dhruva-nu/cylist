"""Getting back in without a password or a passkey (CYLIST-204).

There is no "forgot password" and no email: nothing here can be started by
somebody who is not already one of the person's own signed-in devices, or the
operator of the server.

**From a signed-in device.** A new device asks for recovery by email and is
shown a short code, and keeps a secret the server never shows anywhere else.
A device that is still signed in as that person enters the code, which
approves the request. The new device then claims it with its secret and
is signed in. Codes live ten minutes and are single use: claiming is one
conditional ``UPDATE``, so two devices racing for one code produce one winner.
The code alone is not enough to claim, so reading it off the screen helps
nobody, and the email alone is not enough to start anything that matters, so
an unknown address behaves exactly like a known one and learns nothing.

**From the server.** :func:`operator_reset` is for somebody with a shell on the
deployment, via ``python -m app.cli recover``. Not reachable over HTTP.

Either way, claiming ends every other session and API token the person holds
(:func:`app.services.tokens.revoke_for_person`): recovery is what you do when
you cannot trust what is signed in.
"""

from __future__ import annotations

import secrets
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.tokens import hash_token
from app.core.clock import now
from app.core.errors import UnprocessableRequestError
from app.models.passkey import PersonCredential, RecoveryOrigin, RecoveryRequest
from app.models.person import Person
from app.services import tokens

RECOVERY_TTL = timedelta(minutes=10)
OPERATOR_CODE_TTL = timedelta(hours=1)

_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
"""Upper case and digits, without the look-alikes (0/O, 1/I/L)."""

DEVICE_CODE_LENGTH = 8
OPERATOR_CODE_LENGTH = 16

INVALID_CODE = "That code is not valid, or it has expired."


def normalise(code: str) -> str:
    """What was typed, folded to what was shown: ``abcd-efgh`` is ``ABCDEFGH``."""
    return "".join(ch for ch in code.upper() if ch.isalnum())


def _new_code(length: int) -> str:
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def display(code: str) -> str:
    """The code in groups of four, for reading aloud and typing."""
    return "-".join(code[i : i + 4] for i in range(0, len(code), 4))


async def _find_person(session: AsyncSession, email: str) -> Person | None:
    found: Person | None = await session.scalar(
        select(Person).where(
            func.lower(Person.email) == email.strip().lower(),
            Person.password_hash.is_not(None),
            Person.archived_at.is_(None),
            Person.is_agent.is_(False),
        )
    )
    return found


async def _unused_code(session: AsyncSession, length: int) -> str:
    while True:
        code = _new_code(length)
        taken = await session.scalar(
            select(RecoveryRequest.id).where(RecoveryRequest.code_hash == hash_token(code))
        )
        if taken is None:
            return code


async def start(
    session: AsyncSession, email: str
) -> tuple[RecoveryRequest, str, str, Person | None]:
    """A new device asks to be let in: returns the request, the code to show,
    the secret to keep, and the person it is for (``None`` if the email names nobody).

    Asking again replaces the person's earlier unclaimed requests, so there is
    one live code per person at a time.
    """
    await session.execute(delete(RecoveryRequest).where(RecoveryRequest.expires_at <= now()))
    person = await _find_person(session, email)
    if person is not None:
        await session.execute(
            delete(RecoveryRequest).where(
                RecoveryRequest.person_id == person.id,
                RecoveryRequest.origin == RecoveryOrigin.DEVICE,
                RecoveryRequest.claimed_at.is_(None),
            )
        )
    code = await _unused_code(session, DEVICE_CODE_LENGTH)
    secret = secrets.token_urlsafe(32)
    request = RecoveryRequest(
        origin=RecoveryOrigin.DEVICE,
        person_id=person.id if person is not None else None,
        code_hash=hash_token(code),
        secret_hash=hash_token(secret),
        expires_at=now() + RECOVERY_TTL,
    )
    session.add(request)
    await session.flush()
    return request, code, secret, person


async def approve(
    session: AsyncSession, person_id: UUID, token_id: UUID, code: str
) -> RecoveryRequest:
    """Approve the request this code belongs to, as the person it is for.

    Another person's code, an unknown one, a spent one and an expired one are
    one refusal.

    Raises:
        UnprocessableRequestError: with :data:`INVALID_CODE`.
    """
    approved = await session.scalar(
        update(RecoveryRequest)
        .where(
            RecoveryRequest.code_hash == hash_token(normalise(code)),
            RecoveryRequest.origin == RecoveryOrigin.DEVICE,
            RecoveryRequest.person_id == person_id,
            RecoveryRequest.approved_at.is_(None),
            RecoveryRequest.claimed_at.is_(None),
            RecoveryRequest.expires_at > now(),
        )
        .values(approved_at=now(), approved_by_token_id=token_id)
        .returning(RecoveryRequest)
    )
    if approved is None:
        raise UnprocessableRequestError(INVALID_CODE, log_message="Recovery approval refused.")
    return approved


async def claim(session: AsyncSession, request_id: UUID, secret: str) -> Person | str:
    """Spend an approved request, returning the person to sign in.

    Returns ``"pending"`` while nobody has approved yet, which is not an error:
    the new device is polling.

    Raises:
        UnprocessableRequestError: unknown id, wrong secret, expired or spent.
    """
    request = await session.get(RecoveryRequest, request_id)
    if (
        request is None
        or request.origin != RecoveryOrigin.DEVICE
        or request.secret_hash is None
        or not secrets.compare_digest(request.secret_hash, hash_token(secret))
        or request.claimed_at is not None
        or request.expires_at <= now()
    ):
        raise UnprocessableRequestError(INVALID_CODE, log_message="Recovery claim refused.")
    if request.approved_at is None:
        # Also what a request for an email that names nobody says until it
        # expires: nothing tells it apart from one still waiting for approval.
        return "pending"
    return await _spend(session, request)


async def redeem_operator_code(session: AsyncSession, code: str) -> Person:
    """Spend a code the operator made on the server.

    Raises:
        UnprocessableRequestError: unknown, spent or expired.
    """
    request = await session.scalar(
        select(RecoveryRequest).where(
            RecoveryRequest.code_hash == hash_token(normalise(code)),
            RecoveryRequest.origin == RecoveryOrigin.OPERATOR,
        )
    )
    if request is None or request.claimed_at is not None or request.expires_at <= now():
        raise UnprocessableRequestError(INVALID_CODE, log_message="Operator code refused.")
    return await _spend(session, request)


async def _spend(session: AsyncSession, request: RecoveryRequest) -> Person:
    """Mark the request claimed — once — and end everything else the person holds."""
    won = await session.scalar(
        update(RecoveryRequest)
        .where(
            RecoveryRequest.id == request.id,
            RecoveryRequest.claimed_at.is_(None),
            RecoveryRequest.approved_at.is_not(None),
            RecoveryRequest.expires_at > now(),
        )
        .values(claimed_at=now())
        .returning(RecoveryRequest.id)
    )
    person = await session.get(Person, request.person_id) if request.person_id else None
    if won is None or person is None or person.is_archived:
        raise UnprocessableRequestError(INVALID_CODE, log_message="Recovery claim lost the race.")
    await end_everything_else(session, person.id)
    return person


async def end_everything_else(session: AsyncSession, person_id: UUID) -> int:
    """Revoke every live session and API token of this person, and any other
    recovery still waiting. Returns how many credentials went."""
    await session.execute(
        delete(RecoveryRequest).where(
            RecoveryRequest.person_id == person_id, RecoveryRequest.claimed_at.is_(None)
        )
    )
    return await tokens.revoke_for_person(session, person_id)


async def operator_reset(
    session: AsyncSession, person: Person, *, lock_password: bool, ttl: timedelta
) -> tuple[str, int, int]:
    """Break glass: end every credential, drop every passkey, and mint a code.

    Returns ``(code, credentials_revoked, passkeys_dropped)``. The code is
    printed once to whoever runs the command, who hands it to the person; they
    redeem it at the sign-in screen and are signed in to add a passkey.
    ``lock_password`` also switches the password off (``person.password_locked``).
    """
    revoked = await end_everything_else(session, person.id)
    dropped = await session.execute(
        delete(PersonCredential)
        .where(PersonCredential.person_id == person.id)
        .returning(PersonCredential.id)
    )
    if lock_password:
        person.password_locked = True
    code = await _unused_code(session, OPERATOR_CODE_LENGTH)
    session.add(
        RecoveryRequest(
            origin=RecoveryOrigin.OPERATOR,
            person_id=person.id,
            code_hash=hash_token(code),
            approved_at=now(),
            expires_at=now() + ttl,
        )
    )
    await session.flush()
    return code, revoked, len(dropped.all())
