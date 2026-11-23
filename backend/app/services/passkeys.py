"""Passkeys: registering them, signing in with them, and managing them (CYLIST-204).

The cryptography is ``py_webauthn``'s. What is ours is everything around it:

* **Challenges** are made here, kept in ``passkey_challenge``, and spent in a
  transaction of their own (:func:`_spend`). Spending commits whether or not
  the response then verifies, so one challenge is one attempt: a wrong answer
  cannot be retried against the same value, and a right one cannot be
  replayed. They live five minutes.
* **Origin and RP ID** come from configuration (``Settings.passkey_rp_id``,
  ``passkey_origins``), never from the request, so a passkey is bound to the
  deployment's own domain.
* **Sign-in is usernameless.** The device offers a discoverable credential and
  the server finds the person by its id, so there is no account lookup by
  email and nothing for a stranger to learn about who has one. User
  verification (fingerprint, face or PIN) is required, otherwise a passkey
  would be only something you hold.
* **The counter** must go up. A response whose ``sign_count`` does not pass
  the stored one is a cloned authenticator and is refused; an authenticator
  that keeps no counter reports zero every time, and zero against zero is
  accepted.
"""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    options_to_json,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import parse_registration_credential_json
from webauthn.helpers.exceptions import WebAuthnException
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from app.config import Settings
from app.core.clock import now
from app.core.errors import (
    ConflictError,
    NotFoundError,
    Reason,
    ServiceUnavailableError,
    UnauthorizedError,
    UnprocessableRequestError,
)
from app.db import Database
from app.models.passkey import ChallengePurpose, PasskeyChallenge, PersonCredential
from app.models.person import Person

CHALLENGE_TTL = timedelta(minutes=5)
MAX_NAME_LENGTH = 80
MAX_PASSKEYS = 10
"""How many one person may hold. More than anybody has devices, and what keeps
the list on their settings page short enough to read."""


class PasskeyRefusal(Exception):  # noqa: N818 - a reason, not an error to catch around
    """A sign-in that was not accepted, and the reason for the log alone."""

    def __init__(self, reason: str, person_id: UUID | None = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.person_id = person_id


def _relying_party(settings: Settings) -> tuple[str, list[str]]:
    rp_id = settings.passkey_rp_id
    origins = settings.passkey_origins
    if rp_id is None or not origins:
        raise ServiceUnavailableError(
            "Passkeys are not set up on this deployment: it has no public address to "
            "bind them to. Set CYLIST_CLIENT_URLS or CYLIST_WEBAUTHN_RP_ID.",
            reason=Reason.PASSKEYS_NOT_CONFIGURED,
        )
    return rp_id, origins


def _user_handle(person: Person) -> bytes:
    """The opaque id the authenticator files the passkey under: the person's id."""
    return person.id.bytes


async def _issue(
    session: AsyncSession, purpose: ChallengePurpose, challenge: bytes, person_id: UUID | None
) -> UUID:
    await session.execute(delete(PasskeyChallenge).where(PasskeyChallenge.expires_at <= now()))
    row = PasskeyChallenge(
        purpose=purpose,
        challenge=challenge,
        person_id=person_id,
        expires_at=now() + CHALLENGE_TTL,
    )
    session.add(row)
    await session.flush()
    return row.id


async def _spend(
    database: Database, challenge_id: UUID, purpose: ChallengePurpose, person_id: UUID | None
) -> bytes:
    """Take a challenge out of the table for good and return its bytes.

    A transaction of its own, so the delete survives the request being
    refused afterwards. Expired, unknown, spent and somebody else's are one
    refusal.
    """
    async with database.session() as spending:
        row = await spending.scalar(
            delete(PasskeyChallenge)
            .where(PasskeyChallenge.id == challenge_id, PasskeyChallenge.purpose == purpose)
            .returning(PasskeyChallenge)
        )
    if row is None or row.expires_at <= now() or row.person_id != person_id:
        raise PasskeyRefusal("bad_challenge", person_id)
    return row.challenge


# --- Registering --------------------------------------------------------------


async def begin_registration(
    session: AsyncSession, settings: Settings, person: Person
) -> tuple[UUID, dict[str, Any]]:
    """Options for ``navigator.credentials.create()``, and the id of the challenge."""
    rp_id, _ = _relying_party(settings)
    existing = await credentials_of(session, person.id)
    if len(existing) >= MAX_PASSKEYS:
        raise ConflictError(
            f"You already have {MAX_PASSKEYS} passkeys. Remove one before adding another."
        )
    options = generate_registration_options(
        rp_id=rp_id,
        rp_name=settings.webauthn_rp_name,
        user_id=_user_handle(person),
        user_name=person.email or person.name,
        user_display_name=person.name,
        exclude_credentials=[PublicKeyCredentialDescriptor(id=c.credential_id) for c in existing],
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.REQUIRED,
            user_verification=UserVerificationRequirement.REQUIRED,
        ),
    )
    challenge_id = await _issue(session, ChallengePurpose.REGISTER, options.challenge, person.id)
    return challenge_id, json.loads(options_to_json(options))


async def finish_registration(
    session: AsyncSession,
    database: Database,
    settings: Settings,
    person: Person,
    *,
    challenge_id: UUID,
    credential: dict[str, Any],
    name: str,
) -> PersonCredential:
    """Verify what ``create()`` returned and keep the public key.

    Raises:
        UnprocessableRequestError: the challenge is spent or expired, or the
            device's answer does not verify against this deployment's origin
            and RP ID.
        ConflictError: this authenticator is already registered.
    """
    rp_id, origins = _relying_party(settings)
    try:
        challenge = await _spend(database, challenge_id, ChallengePurpose.REGISTER, person.id)
        verified = verify_registration_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origins,
            require_user_verification=True,
        )
        transports = list(parse_registration_credential_json(credential).response.transports or [])
    except (PasskeyRefusal, WebAuthnException, ValueError, KeyError, TypeError) as exc:
        raise UnprocessableRequestError(
            "That passkey could not be verified. Start again.",
            log_message=f"Passkey registration refused for person {person.id}: {exc!r}",
        ) from exc

    if await session.scalar(
        select(PersonCredential.id).where(PersonCredential.credential_id == verified.credential_id)
    ):
        raise ConflictError("That passkey is already registered.")

    row = PersonCredential(
        person_id=person.id,
        credential_id=verified.credential_id,
        public_key=verified.credential_public_key,
        sign_count=verified.sign_count,
        transports=[str(t.value if hasattr(t, "value") else t) for t in transports],
        name=clean_name(name),
    )
    session.add(row)
    await session.flush()
    return row


# --- Signing in ---------------------------------------------------------------


async def begin_login(session: AsyncSession, settings: Settings) -> tuple[UUID, dict[str, Any]]:
    """Options for ``navigator.credentials.get()``. Names nobody."""
    rp_id, _ = _relying_party(settings)
    options = generate_authentication_options(
        rp_id=rp_id, user_verification=UserVerificationRequirement.REQUIRED
    )
    challenge_id = await _issue(session, ChallengePurpose.LOGIN, options.challenge, None)
    return challenge_id, json.loads(options_to_json(options))


async def finish_login(
    session: AsyncSession,
    database: Database,
    settings: Settings,
    *,
    challenge_id: UUID,
    credential: dict[str, Any],
) -> Person:
    """The person a valid assertion belongs to, with their counter advanced.

    Raises:
        PasskeyRefusal: for any reason at all; the caller answers every one the
            same way and logs this one.
    """
    rp_id, origins = _relying_party(settings)
    challenge = await _spend(database, challenge_id, ChallengePurpose.LOGIN, None)
    try:
        raw_id = base64url_to_bytes(str(credential["rawId"]))
    except (KeyError, ValueError, TypeError) as exc:
        raise PasskeyRefusal("malformed") from exc

    row = await session.scalar(
        select(PersonCredential).where(PersonCredential.credential_id == raw_id)
    )
    if row is None:
        raise PasskeyRefusal("unknown_credential")
    person = await session.get(Person, row.person_id)
    if person is None or person.is_archived:
        raise PasskeyRefusal("archived_account", row.person_id)

    try:
        handle = (credential.get("response") or {}).get("userHandle")
        if handle and base64url_to_bytes(str(handle)) != _user_handle(person):
            raise PasskeyRefusal("wrong_user_handle", person.id)
        verified = verify_authentication_response(
            credential=credential,
            expected_challenge=challenge,
            expected_rp_id=rp_id,
            expected_origin=origins,
            credential_public_key=row.public_key,
            credential_current_sign_count=row.sign_count,
            require_user_verification=True,
        )
    except WebAuthnException as exc:
        raise PasskeyRefusal("verification_failed", person.id) from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise PasskeyRefusal("malformed", person.id) from exc

    new_count = verified.new_sign_count
    if (new_count or row.sign_count) and new_count <= row.sign_count:
        raise PasskeyRefusal("sign_count_not_increasing", person.id)
    row.sign_count = new_count
    row.last_used_at = now()
    return person


# --- Managing -----------------------------------------------------------------


def clean_name(name: str) -> str:
    cleaned = " ".join(name.split())[:MAX_NAME_LENGTH]
    return cleaned or "Passkey"


async def credentials_of(session: AsyncSession, person_id: UUID) -> list[PersonCredential]:
    rows = await session.scalars(
        select(PersonCredential)
        .where(PersonCredential.person_id == person_id)
        .order_by(PersonCredential.created_at, PersonCredential.id)
    )
    return list(rows)


async def get_own(session: AsyncSession, person_id: UUID, credential_id: UUID) -> PersonCredential:
    """One of this person's passkeys; somebody else's is as absent as an unknown id."""
    row = await session.get(PersonCredential, credential_id)
    if row is None or row.person_id != person_id:
        raise NotFoundError("No passkey of yours with that id.")
    return row


async def rename(session: AsyncSession, row: PersonCredential, name: str) -> str:
    """Rename a passkey, returning the name it had."""
    old = row.name
    row.name = clean_name(name)
    await session.flush()
    return old


async def delete_passkey(session: AsyncSession, person: Person, row: PersonCredential) -> None:
    """Remove a passkey, unless it is the last way this person can sign in.

    Raises:
        UnprocessableRequestError: it is their only passkey and their password
            is off, so removing it would lock them out.
    """
    if person.password_locked:
        others = [c for c in await credentials_of(session, person.id) if c.id != row.id]
        if not others:
            raise UnprocessableRequestError(
                "This is your only passkey and your password is switched off. Add another "
                "passkey before removing it.",
                reason=Reason.LAST_PASSKEY,
                log_message=f"Refused removing person {person.id}'s last passkey.",
            )
    await session.delete(row)
    await session.flush()


def refusal_message() -> UnauthorizedError:
    """What every failed passkey sign-in says."""
    return UnauthorizedError("That passkey was not accepted.")
