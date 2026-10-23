"""Audit trail.

Every mutation writes one row. Because agents act through the same API as the
browser, this is the only place that answers "what did the CLI change at 3am?".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy import text as sql_text
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
        Index("ix_activity_actor_person_id_occurred_at", "actor_person_id", "occurred_at"),
        # Only the rows waiting for the courier, which are few and recent.
        Index(
            "ix_activity_hooks_pending",
            "occurred_at",
            "id",
            postgresql_where=sql_text("hooks_pending"),
        ),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor_token_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("api_token.id", ondelete="SET NULL"),
    )
    actor_person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Which person was behind the change, when one was.

    The token id alone stopped being enough the moment more than one person
    could hold one: "what did the CLI change at 3am" is now also "and whose
    CLI was it". Nulled rather than cascaded for the reason
    :attr:`project_id` is — the record of what happened outlives the
    directory entry — and null for the bootstrap session, which has no person.
    """

    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    """Readable actor name, kept even if the token row is later deleted.

    For a person's own session this is their name, so a feed read years later
    still says who did it without a join that may no longer resolve.
    """

    channel: Mapped[Channel] = mapped_column(
        Enum(
            Channel,
            name="channel",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    verb: Mapped[str] = mapped_column(String(64), nullable=False)
    """Dotted past-tense event name, e.g. ``task.status_changed``."""

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))

    project_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="SET NULL"),
    )
    """Scopes the feed to one project. Nulled rather than cascaded if a project
    ever goes away, so the record of what happened outlives it."""

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    hooks_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    """Whether the hooks still have to be matched against this change. Set by the
    write that records it, cleared by the courier when it has queued whatever
    deliveries the change earned — see :func:`app.services.hooks.match_pending`."""


class ActivityArchive(Base):
    """Trail rows old enough to have been moved off the hot table — CYLIST-139.

    Every mutation writes an ``activity`` row and nothing ever removed one, so
    the table that every card's history and every day report reads grew by
    every agent turn, forever. Rows older than
    :attr:`app.config.Settings.activity_retention_days` are moved here, whole,
    by :func:`app.services.retention.archive_activity`.

    Cold on purpose: nothing in the application reads it, so it carries no
    index but its key, and no foreign keys — the record of what happened
    outlives the token, person and project it names, which is the same promise
    the hot table makes by nulling them, kept here by never pointing at them at
    all. ``channel`` is plain text for the same reason: a row archived under
    one set of channels must still be insertable after that set changes.
    """

    __tablename__ = "activity_archive"

    id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    """The hot row's own id, kept so an archived row can be matched to
    anything that recorded it — a hook delivery's log, say."""

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    actor_person_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    verb: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
