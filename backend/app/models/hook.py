"""Hooks: a project's rules for telling the outside world what changed.

A **hook** is one rule on one project: "when a card of the Hotfix template is
moved to In staging, POST it to this URL". Every change on a board is already
an :class:`~app.models.activity.Activity` row with a verb; a hook names the
verbs it cares about and narrows them with a few filters about the card.

A **delivery** is one event on its way to one hook's URL. It is written in the
same transaction as the change it reports, so a change that rolls back sends
nothing and a change that commits cannot be forgotten by a crash before the
POST — the worker in :mod:`app.services.hook_delivery` sends whatever is due,
and retries what the receiver did not take.

Two choices worth stating:

* **No verbs means every verb.** Silence is not a ban, the same reading a
  template with no stages gets: a hook that wants everything a board does
  should not have to list it, nor go quiet when a new verb is added.
* **The filter columns hold ids, not foreign keys.** A filter naming a column
  that has since been deleted matches nothing, and the hook goes quiet rather
  than loud. A foreign key could only cascade — deleting the user's rule
  without asking — or null the filter, which would turn "moved to Done" into
  "moved anywhere" the moment Done was deleted.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.task import TaskType

NAME_MAX_LENGTH = 80
URL_MAX_LENGTH = 2000
VERB_MAX_LENGTH = 64
"""Matches ``activity.verb``: a hook names the same words the trail records."""


def _enum(python_type: type[StrEnum], name: str) -> Enum:
    """Values in a VARCHAR plus a CHECK, as every other enum here is stored."""
    return Enum(
        python_type,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda enum: [member.value for member in enum],
    )


class Hook(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One rule: which changes on a project to send, and where."""

    __tablename__ = "hook"
    __table_args__ = (Index("ix_hook_project_id_name", "project_id", "name", unique=True),)

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    """Unique on the project, so a delivery log can say which rule fired."""

    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=sql_text("true")
    )
    """Off stops new deliveries being queued. Ones already queued still go:
    they report changes that happened while the hook was on."""

    verbs: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(VERB_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'"),
    )
    """The activity verbs this hook fires on — ``task.moved``, ``task.created``.
    Empty means all of them; see the module docstring."""

    to_column_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """The card's column after the change. On a move, only a move that changed
    column — into this one — matches."""

    from_column_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Only a move out of this column matches."""

    template_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Only cards of this template match."""

    task_type: Mapped[TaskType | None] = mapped_column(_enum(TaskType, "task_type"))
    """Only cards of this type match."""

    url: Mapped[str] = mapped_column(String(URL_MAX_LENGTH), nullable=False)
    """Where events are POSTed. ``http`` or ``https``."""

    secret_ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    """The signing secret, sealed with the vault key under its own domain — see
    :data:`app.core.crypto.HOOK_SECRET_DOMAIN`. Kept rather than hashed because
    every delivery has to sign with it."""

    key_version: Mapped[int] = mapped_column(Integer, nullable=False)

    secret_hint: Mapped[str] = mapped_column(String(8), nullable=False)
    """The secret's last four characters, so a page can tell two secrets apart
    without decrypting either."""


class DeliveryState(StrEnum):
    """Where an event is on its way to a hook's URL."""

    PENDING = "pending"
    """Not yet taken: either never tried, or tried and due to be tried again."""
    DELIVERED = "delivered"
    """The receiver answered 2xx."""
    FAILED = "failed"
    """Every attempt was refused or went unanswered. Nothing more is tried
    unless someone asks for it to be sent again."""


class HookDelivery(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One event on its way to one hook's URL, and every attempt to get it there."""

    __tablename__ = "hook_delivery"
    __table_args__ = (
        Index("ix_hook_delivery_hook_id_created_at", "hook_id", "created_at"),
        # What the worker asks on every pass, and only ever of pending rows.
        Index(
            "ix_hook_delivery_next_attempt_at",
            "next_attempt_at",
            postgresql_where=sql_text("state = 'pending'"),
        ),
    )

    hook_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("hook.id", ondelete="CASCADE"),
        nullable=False,
    )

    activity_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("activity.id", ondelete="SET NULL"),
    )
    """The change this reports. Null for a test send, which reports nothing."""

    event: Mapped[str] = mapped_column(String(VERB_MAX_LENGTH), nullable=False)
    """The verb, or ``hook.test``."""

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    """Exactly the body sent, kept so a retry sends what the first attempt did
    rather than whatever the card says by then."""

    state: Mapped[DeliveryState] = mapped_column(
        _enum(DeliveryState, "delivery_state"),
        nullable=False,
        default=DeliveryState.PENDING,
        server_default=sql_text("'pending'"),
    )

    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )

    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the worker may next take it. Null once it is delivered or failed."""

    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    last_status_code: Mapped[int | None] = mapped_column(Integer)

    last_error: Mapped[str | None] = mapped_column(Text)
    """Why the last attempt did not count — a status line and the start of the
    body, or the network error. Null after a success."""

    attempts: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=sql_text("'[]'")
    )
    """Every attempt, oldest first: ``{at, status_code, error, duration_ms}``.
    A list on the row rather than rows of their own: there are at most
    :data:`app.services.hook_delivery.MAX_ATTEMPTS` of them, they are only ever
    read with the delivery, and nothing points at one."""
