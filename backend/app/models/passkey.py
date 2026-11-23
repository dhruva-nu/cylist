"""Passkeys and the recovery that stands in for a forgotten password (CYLIST-204).

A passkey is a key pair the person's device made and keeps behind a fingerprint,
face or PIN. The server holds the public half only (``person_credential``), so
nothing here is a secret a database leak could turn into a sign-in, and no
biometric ever leaves the device.

Two short-lived tables hold the ceremonies. A ``passkey_challenge`` is the
random value a device must sign; it lives in the database rather than in a
process, so a request may land on any replica, and it is deleted the moment it
is spent. A ``recovery_request`` is a new device asking to be let in by one
that is still signed in.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, LargeBinary, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


def _enum[E: StrEnum](cls: type[E], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


class PersonCredential(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One passkey of one person."""

    __tablename__ = "person_credential"
    __table_args__ = (
        Index("ix_person_credential_credential_id", "credential_id", unique=True),
        Index("ix_person_credential_person_id", "person_id"),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE"), nullable=False
    )
    credential_id: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """What the authenticator calls this key; unique across the table, because
    sign-in finds the person by it."""

    public_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """COSE-encoded public key. Verifies a signature; signs nothing."""

    sign_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """The authenticator's use counter at its last sign-in. A sign-in whose
    counter does not go past it is a cloned key and is refused — unless both
    are zero, which is how a synced passkey says it keeps no counter."""

    transports: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    """Chosen by the person ("OnePlus phone"), so the list can be told apart."""

    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ChallengePurpose(StrEnum):
    REGISTER = "register"
    LOGIN = "login"


class PasskeyChallenge(Base, UUIDPrimaryKeyMixin):
    """A challenge handed out and not yet spent. Deleted on use, by anybody."""

    __tablename__ = "passkey_challenge"
    __table_args__ = (Index("ix_passkey_challenge_expires_at", "expires_at"),)

    purpose: Mapped[ChallengePurpose] = mapped_column(
        _enum(ChallengePurpose, "challenge_purpose"), nullable=False
    )
    challenge: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE")
    )
    """Whom a registration challenge was made for, so another person's
    session cannot spend it. Null for a sign-in, which names nobody yet."""

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class RecoveryOrigin(StrEnum):
    DEVICE = "device"
    """A new device asked; a signed-in one must approve."""

    OPERATOR = "operator"
    """Made by the deployment's operator on the server, already approved."""


class RecoveryRequest(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A device asking to be let in without a passkey or a password."""

    __tablename__ = "recovery_request"
    __table_args__ = (
        Index("ix_recovery_request_code_hash", "code_hash", unique=True),
        Index("ix_recovery_request_expires_at", "expires_at"),
    )

    origin: Mapped[RecoveryOrigin] = mapped_column(
        _enum(RecoveryOrigin, "recovery_origin"), nullable=False
    )
    person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True), ForeignKey("person.id", ondelete="CASCADE")
    )
    """Whom it is for. Null when the email named nobody: the request is made
    anyway and can never be approved, so asking tells a stranger nothing."""

    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    """Digest of the code shown on the new device. Only the digest is kept."""

    secret_hash: Mapped[str | None] = mapped_column(String(64))
    """Digest of the secret the new device holds, which the code alone cannot
    stand in for: somebody who reads the code off the screen cannot claim."""

    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by_token_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
