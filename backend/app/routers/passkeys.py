"""Passkeys, and recovery in place of a forgotten password (CYLIST-204).

Signing in with a passkey issues the same session cookie a password does, and
is throttled by the same buckets (:mod:`app.auth.throttle`). See
:mod:`app.services.passkeys` for how challenges, origins and the sign counter
are handled, and :mod:`app.services.recovery` for the recovery flows.
"""

from __future__ import annotations

import logging
from typing import Any, NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core.errors import ForbiddenError
from app.core.metrics import live
from app.core.request_log import client_address
from app.db import SessionDependency, database_of
from app.models.activity import Channel, EntityType, Verb
from app.models.passkey import PersonCredential
from app.models.person import Person
from app.routers.auth import _start_session, _throttle
from app.schemas.auth import Identity
from app.schemas.common import Acknowledged
from app.schemas.passkeys import (
    CeremonyOptions,
    PasskeyLogin,
    PasskeyRead,
    PasskeyRegistered,
    PasskeyRegistration,
    PasskeyRename,
    RecoveryApprove,
    RecoveryClaim,
    RecoveryRedeem,
    RecoveryStart,
    RecoveryStarted,
    RecoveryState,
)
from app.services import activity, passkeys, people, recovery

logger = logging.getLogger(__name__)

router = APIRouter(tags=["passkeys"])

_RATE_LIMITED: dict[int | str, dict[str, Any]] = {
    429: {"description": "Too many attempts; `Retry-After` says how long to wait."}
}


def _person_id(principal: Principal) -> UUID:
    if principal.person_id is None:
        raise ForbiddenError(
            "The bootstrap session belongs to nobody and cannot hold a passkey. "
            "Create an account under People first."
        )
    return principal.person_id


def _read(row: PersonCredential) -> PasskeyRead:
    return PasskeyRead.model_validate(row)


def _refuse(request: Request, refusal: passkeys.PasskeyRefusal) -> NoReturn:
    """Log why, count it, and give every cause the same answer."""
    live().auth_refusals[f"passkey_{refusal.reason}"] += 1
    logger.warning(
        "Failed passkey sign-in",
        extra={
            "context": {
                "client": client_address(request),
                "reason": refusal.reason,
                "person_id": str(refusal.person_id) if refusal.person_id else None,
            }
        },
    )
    raise passkeys.refusal_message()


# --- Registering and managing -------------------------------------------------


@router.post(
    "/auth/passkeys/register/options",
    response_model=CeremonyOptions,
    summary="Start adding a passkey",
    responses={**_RATE_LIMITED, 503: {"description": "This deployment has no passkey domain."}},
)
async def register_options(
    request: Request,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> CeremonyOptions:
    """Options for `navigator.credentials.create()`, for the signed-in person."""
    person_id = _person_id(principal)
    _throttle(request, account=f"passkey-register:{person_id}")
    person = await people.get(session, person_id)
    challenge_id, options = await passkeys.begin_registration(session, settings, person)
    return CeremonyOptions(challenge_id=challenge_id, options=options)


@router.post(
    "/auth/passkeys/register/verify",
    response_model=PasskeyRegistered,
    summary="Finish adding a passkey",
    responses={
        409: {"description": "That authenticator is already registered."},
        422: {"description": "The challenge is spent or the answer did not verify."},
        **_RATE_LIMITED,
    },
)
async def register_verify(
    body: PasskeyRegistration,
    request: Request,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> PasskeyRegistered:
    """Verify the browser's answer and keep the public key.

    `recommend_another` is true until the person has two passkeys: the client
    should prompt for a second device, because one lost phone must not be the
    end of the account.
    """
    person_id = _person_id(principal)
    _throttle(request, account=f"passkey-register:{person_id}")
    person = await people.get(session, person_id)
    row = await passkeys.finish_registration(
        session,
        database_of(request),
        settings,
        person,
        challenge_id=body.challenge_id,
        credential=body.credential,
        name=body.name,
    )
    await activity.record(
        session,
        principal,
        Verb.PASSKEY_REGISTERED,
        entity_type=EntityType.PASSKEY,
        entity_id=row.id,
        payload={"name": row.name},
    )
    count = len(await passkeys.credentials_of(session, person_id))
    return PasskeyRegistered(passkey=_read(row), passkey_count=count, recommend_another=count < 2)


@router.get(
    "/me/passkeys",
    response_model=list[PasskeyRead],
    summary="List your passkeys",
    responses={403: {"description": "The bootstrap session has no person behind it."}},
)
async def my_passkeys(
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[PasskeyRead]:
    return [_read(row) for row in await passkeys.credentials_of(session, _person_id(principal))]


@router.patch(
    "/me/passkeys/{passkey_id}",
    response_model=PasskeyRead,
    summary="Rename one of your passkeys",
    responses={404: {"description": "No passkey of yours with that id."}},
)
async def rename_passkey(
    passkey_id: UUID,
    body: PasskeyRename,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> PasskeyRead:
    row = await passkeys.get_own(session, _person_id(principal), passkey_id)
    old = await passkeys.rename(session, row, body.name)
    await activity.record(
        session,
        principal,
        Verb.PASSKEY_RENAMED,
        entity_type=EntityType.PASSKEY,
        entity_id=row.id,
        payload={"old_name": old, "name": row.name},
    )
    return _read(row)


@router.delete(
    "/me/passkeys/{passkey_id}",
    response_model=Acknowledged,
    summary="Remove one of your passkeys",
    responses={
        404: {"description": "No passkey of yours with that id."},
        422: {"description": "It is your last way in: your password is switched off."},
    },
)
async def delete_passkey(
    passkey_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Remove a passkey. Refused for the last one while the password is off."""
    person_id = _person_id(principal)
    person = await people.get(session, person_id)
    row = await passkeys.get_own(session, person_id, passkey_id)
    name = row.name
    await passkeys.delete_passkey(session, person, row)
    await activity.record(
        session,
        principal,
        Verb.PASSKEY_DELETED,
        entity_type=EntityType.PASSKEY,
        entity_id=passkey_id,
        payload={"name": name},
    )
    return Acknowledged()


# --- Signing in ---------------------------------------------------------------


@router.post(
    "/auth/passkeys/login/options",
    response_model=CeremonyOptions,
    summary="Start signing in with a passkey",
    responses={**_RATE_LIMITED, 503: {"description": "This deployment has no passkey domain."}},
)
async def login_options(
    request: Request,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> CeremonyOptions:
    """Options for `navigator.credentials.get()`. Takes and names nobody: the
    device offers whichever passkey it holds for this site."""
    _throttle(request, account=None)
    challenge_id, options = await passkeys.begin_login(session, settings)
    return CeremonyOptions(challenge_id=challenge_id, options=options)


@router.post(
    "/auth/passkeys/login/verify",
    response_model=Identity,
    summary="Sign in with a passkey",
    responses={
        401: {"description": "The passkey was not accepted."},
        **_RATE_LIMITED,
    },
)
async def login_verify(
    body: PasskeyLogin,
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Verify the assertion and issue the session cookie a password login would.

    Every way of failing — unknown passkey, reused or expired challenge, wrong
    signature, a counter that did not advance, an archived account — is the
    same 401.
    """
    _throttle(request, account=None)
    try:
        person = await passkeys.finish_login(
            session,
            database_of(request),
            settings,
            challenge_id=body.challenge_id,
            credential=body.credential,
        )
    except passkeys.PasskeyRefusal as refusal:
        _refuse(request, refusal)
    identity, _ = await _start_session(
        session, request, response, settings, person, method="passkey"
    )
    return identity


# --- Recovery -----------------------------------------------------------------


def _principal_of(person_id: UUID, label: str) -> Principal:
    return Principal(
        token_id=None, person_id=person_id, label=label, scopes=frozenset(), channel=Channel.WEB
    )


@router.post(
    "/auth/recovery/start",
    response_model=RecoveryStarted,
    summary="Ask to be let in from a new device",
    responses=_RATE_LIMITED,
)
async def recovery_start(
    body: RecoveryStart,
    request: Request,
    session: AsyncSession = SessionDependency,
) -> RecoveryStarted:
    """Get a short code to type on a device that is still signed in.

    Answers identically for an address with no account, so it cannot be used
    to find out who has one.
    """
    _throttle(request, account=f"recovery:{body.email.strip().lower()}")
    row, code, secret, person = await recovery.start(session, body.email)
    if person is not None:
        await activity.record(
            session,
            _principal_of(person.id, person.name),
            Verb.RECOVERY_STARTED,
            entity_type=EntityType.PERSON,
            entity_id=person.id,
            payload={"name": person.name, "client": client_address(request)},
        )
    return RecoveryStarted(
        request_id=row.id,
        code=recovery.display(code),
        secret=secret,
        expires_at=row.expires_at,
    )


@router.post(
    "/auth/recovery/approve",
    response_model=Acknowledged,
    summary="Approve a new device from this signed-in one",
    responses={
        422: {"description": "The code is unknown, spent, expired or not yours."},
        **_RATE_LIMITED,
    },
)
async def recovery_approve(
    body: RecoveryApprove,
    request: Request,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Let the device showing this code in. Works from any live session or API
    token of the same person; approve only a code you are looking at on a
    device you are holding."""
    person_id = _person_id(principal)
    _throttle(request, account=f"recovery-approve:{person_id}")
    assert principal.token_id is not None
    await recovery.approve(session, person_id, principal.token_id, body.code)
    await activity.record(
        session,
        principal,
        Verb.RECOVERY_APPROVED,
        entity_type=EntityType.PERSON,
        entity_id=person_id,
    )
    return Acknowledged()


async def _finish_recovery(
    session: AsyncSession,
    request: Request,
    response: Response,
    settings: Settings,
    person: Person,
) -> Identity:
    identity, principal = await _start_session(
        session, request, response, settings, person, method="recovery"
    )
    await activity.record(
        session,
        principal,
        Verb.RECOVERY_COMPLETED,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        payload={"name": person.name},
    )
    return identity


@router.post(
    "/auth/recovery/{request_id}/claim",
    response_model=RecoveryState,
    summary="Collect an approved recovery on the new device",
    responses={
        422: {"description": "Unknown, spent or expired request, or the wrong secret."},
        **_RATE_LIMITED,
    },
)
async def recovery_claim(
    request_id: UUID,
    body: RecoveryClaim,
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> RecoveryState:
    """Poll with the secret from `start`. `pending` until a signed-in device
    approves; then one call signs this device in, ends every other session and
    API token the person holds, and can never be repeated."""
    _throttle(request, account=None)
    outcome = await recovery.claim(session, request_id, body.secret)
    if isinstance(outcome, str):
        return RecoveryState(state="pending")
    identity = await _finish_recovery(session, request, response, settings, outcome)
    return RecoveryState(state="signed_in", identity=identity)


@router.post(
    "/auth/recovery/redeem",
    response_model=Identity,
    summary="Sign in with a code from the operator",
    responses={
        422: {"description": "The code is unknown, spent or expired."},
        **_RATE_LIMITED,
    },
)
async def recovery_redeem(
    body: RecoveryRedeem,
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Spend a code made by `python -m app.cli recover`. Single use."""
    _throttle(request, account="recovery-redeem")
    person = await recovery.redeem_operator_code(session, body.code)
    return await _finish_recovery(session, request, response, settings, person)
