"""Passkeys and recovery (CYLIST-204)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import EmailStr, Field

from app.schemas.auth import Identity
from app.schemas.common import Schema

MAX_CODE_LENGTH = 40


class CeremonyOptions(Schema):
    """What to hand to ``navigator.credentials.create()`` or ``.get()``."""

    challenge_id: UUID = Field(description="Send back with the browser's answer; single use.")
    options: dict[str, Any] = Field(
        description="The WebAuthn options in the JSON form the browser's "
        "`PublicKeyCredential.parseCreationOptionsFromJSON` and `parseRequestOptionsFromJSON` read."
    )


class PasskeyRegistration(Schema):
    challenge_id: UUID
    credential: dict[str, Any] = Field(
        description="`PublicKeyCredential.toJSON()` from `navigator.credentials.create()`."
    )
    name: str = Field(
        default="", max_length=200, description="What to call it, e.g. 'OnePlus phone'."
    )


class PasskeyLogin(Schema):
    challenge_id: UUID
    credential: dict[str, Any] = Field(
        description="`PublicKeyCredential.toJSON()` from `navigator.credentials.get()`."
    )


class PasskeyRead(Schema):
    id: UUID
    name: str
    transports: list[str]
    created_at: datetime
    last_used_at: datetime | None


class PasskeyRename(Schema):
    name: str = Field(min_length=1, max_length=200)


class PasskeyRegistered(Schema):
    passkey: PasskeyRead
    passkey_count: int
    recommend_another: bool = Field(
        description="True while the person has fewer than two: one lost device "
        "must not lock them out."
    )


class RecoveryStart(Schema):
    email: EmailStr


class RecoveryStarted(Schema):
    request_id: UUID
    code: str = Field(description="Show on screen; a signed-in device types it in.")
    secret: str = Field(description="Keep on this device; needed to claim. Never display.")
    expires_at: datetime


class RecoveryApprove(Schema):
    code: str = Field(min_length=1, max_length=MAX_CODE_LENGTH)


class RecoveryClaim(Schema):
    secret: str = Field(min_length=1, max_length=200)


class RecoveryRedeem(Schema):
    code: str = Field(min_length=1, max_length=MAX_CODE_LENGTH)


class RecoveryState(Schema):
    state: Literal["pending", "signed_in"]
    identity: Identity | None = None
