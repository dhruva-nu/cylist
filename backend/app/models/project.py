"""Projects, and who is on them.

A project is the top-level container: it owns a board, a file tree, a vault and
a set of members. Everything else in Cylist hangs off one of these.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import KIND_ORDER, Person

KEY_MAX_LENGTH = 6


class Project(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "project"
    __table_args__ = (
        Index("ix_project_key", "key", unique=True),
        Index("ix_project_archived_at", "archived_at"),
    )

    key: Mapped[str] = mapped_column(String(KEY_MAX_LENGTH), nullable=False)
    """Short uppercase handle — ATL, HRM. Used in task numbers (``ATL-41``) and
    accepted anywhere a project id is, so the CLI can say ``cylist board ATL``."""

    name: Mapped[str] = mapped_column(String(160), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    colour: Mapped[str] = mapped_column(String(7), nullable=False)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Archiving hides a project without touching its board, files or vault.
    There is deliberately no hard delete: a project holds credentials and
    uploads that no confirmation dialog is worth risking."""

    members: Mapped[list[Person]] = relationship(
        secondary="project_member",
        order_by=(KIND_ORDER, Person.name),
        lazy="selectin",  # one extra query per fetch, never N+1
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class ProjectMember(Base, TimestampMixin):
    """Join table putting a person on a project.

    Membership is what the assignee and "waiting on" pickers read, so adding
    someone to a project is what makes them selectable on its board.
    """

    __tablename__ = "project_member"

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        primary_key=True,
    )
