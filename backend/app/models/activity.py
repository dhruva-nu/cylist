"""Audit trail.

Every mutation writes one row. Because agents act through the same API as the
browser, this is the only place that answers "what did the CLI change at 3am?".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class Channel(StrEnum):
    """Which door the actor came through."""

    WEB = "web"
    API = "api"


class Activity(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "activity"
    __table_args__ = (
        Index("ix_activity_occurred_at", "occurred_at"),
        Index("ix_activity_project_id_occurred_at", "project_id", "occurred_at"),
        Index("ix_activity_entity_type_entity_id", "entity_type", "entity_id"),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor_token_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("api_token.id", ondelete="SET NULL"),
    )
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    """Readable actor name, kept even if the token row is later deleted."""

    channel: Mapped[Channel] = mapped_column(
        Enum(
            Channel,
            name="channel",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    verb: Mapped[str] = mapped_column(String(64), nullable=False)
    """Dotted past-tense event name, e.g. ``task.status_changed``."""

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))

    project_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Scopes the feed to one project. The foreign key arrives with the
    ``project`` table in the next migration."""

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
