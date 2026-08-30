"""People: the team and the clients.

The directory is global rather than per-project. A client who appears on three
projects is one row, so their details are edited once and a task can name them
whoever is looking.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Case, DateTime, Enum, Index, String, Text, case
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class PersonKind(StrEnum):
    """Which side of the work someone is on."""

    TEAM = "team"
    """Does the work."""

    CLIENT = "client"
    """Commissions, approves or unblocks the work."""


class Person(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "person"
    __table_args__ = (Index("ix_person_kind_archived_at", "kind", "archived_at"),)

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    kind: Mapped[PersonKind] = mapped_column(
        Enum(
            PersonKind,
            name="person_kind",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(160), nullable=False)
    """Who this is, in one line — "Finance controller, Atlas"."""

    responsibilities: Mapped[str] = mapped_column(Text, nullable=False)
    """What they do, and so what you would tag them about when work stalls."""

    email: Mapped[str | None] = mapped_column(String(254))
    colour: Mapped[str] = mapped_column(String(7), nullable=False)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """People are archived, never deleted: tasks and files keep pointing at
    whoever created them long after they leave the project."""

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


KIND_ORDER: Case[int] = case(
    (Person.kind == PersonKind.TEAM, 0),
    (Person.kind == PersonKind.CLIENT, 1),
    else_=2,
)
"""Sort key putting the team before clients.

Ordering by the column itself would be alphabetical — "client" before "team" —
which reads backwards on a page whose first heading is Team.
"""
