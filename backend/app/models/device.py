"""Push devices: the phones that asked to be told when an agent needs someone.

A phone app registers the token Firebase Cloud Messaging gave its install,
against the person it is signed in as and the credential it signed in with.
:mod:`app.services.push` sends to those tokens when an agent session on a
card changes state (CYLIST-107).

**Keyed by the FCM token.** One install has one token at a time, so the same
token registered again — by the same phone after a restart, or by somebody
else signing in on it — moves the row rather than adding a second one. A
phone handed to someone else stops ringing for the last owner the moment the
new one signs in.

**Tied to the credential.** ``token_id`` is the API token the phone signed in
with. A device is only sent to while that token is live (see
:func:`app.services.devices.recipients`), so revoking the phone's token from
the web app silences it at once even if the phone never says goodbye; and
``CASCADE`` removes the row should the token row itself ever be deleted.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class DevicePlatform(StrEnum):
    """Which push service a device's token belongs to."""

    ANDROID = "android"
    """Firebase Cloud Messaging, through the HTTP v1 API."""


class PushDevice(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "push_device"
    __table_args__ = (
        Index("ix_push_device_fcm_token", "fcm_token", unique=True),
        Index("ix_push_device_person_id", "person_id"),
        Index("ix_push_device_token_id", "token_id"),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Who is told. Whoever the registering credential acts as."""

    token_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("api_token.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The credential that registered it. Pushes stop when it is revoked."""

    platform: Mapped[DevicePlatform] = mapped_column(
        Enum(
            DevicePlatform,
            name="device_platform",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
            length=20,
        ),
        nullable=False,
    )

    fcm_token: Mapped[str] = mapped_column(Text, nullable=False)
    """The registration token Firebase gave this install. Not a secret that
    grants anything on this server, but it is what a push is addressed to, so
    it is never logged and never returned whole."""

    label: Mapped[str] = mapped_column(String(120), nullable=False)
    """What the phone calls itself — "Dhruva's OnePlus (Android)"."""

    last_registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """The last time the app registered this token: every sign-in, and every
    time Firebase rotates it."""
