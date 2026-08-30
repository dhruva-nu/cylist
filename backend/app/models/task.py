"""Tasks: the cards, who a stalled one is waiting on, and their timeline.

A task's whole history — comments people wrote and status changes Cylist
recorded — lives in one table, so a card reads as a single story rather than
two lists the reader has to interleave by timestamp.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Date, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import Person
from app.models.project import Project


class TaskType(StrEnum):
    """What kind of work this is."""

    FEATURE = "feature"
    BUG = "bug"
    CHORE = "chore"


class TaskStatus(StrEnum):
    """Whether the task is moving, and if not, why not."""

    ACTIVE = "active"
    """Nothing is in the way."""

    HOLD = "hold"
    """Deliberately paused — a priority call, or a dependency not yet due."""

    BLOCKED = "blocked"
    """Cannot proceed. Somebody has to do something first."""

    @property
    def label(self) -> str:
        """How this status is worded in a status-change comment."""
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    TaskStatus.ACTIVE: "Active",
    TaskStatus.HOLD: "On hold",
    TaskStatus.BLOCKED: "Blocked",
}


class CommentKind(StrEnum):
    """Who wrote a timeline entry, in effect."""

    COMMENT = "comment"
    """Somebody typed it."""

    STATUS_CHANGE = "status_change"
    """Cylist wrote it when the status moved. ``meta`` carries the detail."""


def _enum(python_type: type[StrEnum], name: str) -> Enum:
    """Store an enum as its values in a VARCHAR, never as a native PG type.

    A native enum needs a migration to gain a member and cannot be altered
    inside a transaction — a lot of ceremony for a list that exists to keep
    three strings honest.
    """
    return Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda enum: [member.value for member in enum],
    )


class Task(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "task"
    __table_args__ = (
        Index("ix_task_project_id_number", "project_id", "number", unique=True),
        Index("ix_task_project_id_column_id_position", "project_id", "column_id", "position"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    """Per-project, handed out by ``project.task_counter`` and never reused.
    With the project's key it forms the reference the task is known by
    everywhere else — ``ATL-41`` in a commit message, a Slack thread, a CLI
    argument — so the same number turning up on a second task would be a lie."""

    column_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("board_column.id"),
        nullable=False,
    )
    """No ``ondelete``: NO ACTION is checked at the end of the statement, so
    deleting a project still cascades cleanly, while deleting a column that
    holds tasks fails rather than quietly taking the tasks with it."""

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Top to bottom within the column, contiguous from zero."""

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    """Required: a card whose title is the whole story is a card nobody else
    can pick up."""

    type: Mapped[TaskType] = mapped_column(_enum(TaskType, "task_type"), nullable=False)

    due_date: Mapped[date] = mapped_column(Date, nullable=False)

    assignee_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id"),
        nullable=False,
    )
    """Required, and always a member of the task's project. Work nobody is
    named on is work nobody has agreed to do."""

    status: Mapped[TaskStatus] = mapped_column(
        _enum(TaskStatus, "task_status"), nullable=False, default=TaskStatus.ACTIVE
    )

    jira_ref: Mapped[str | None] = mapped_column(String(64))
    pr_ref: Mapped[str | None] = mapped_column(String(200))

    project: Mapped[Project] = relationship(lazy="selectin")
    assignee: Mapped[Person] = relationship(lazy="selectin")

    waiting_on: Mapped[list[Person]] = relationship(
        secondary="task_waiting_on",
        order_by=Person.name,
        lazy="selectin",  # one extra query per fetch, never N+1
    )

    @property
    def reference(self) -> str:
        """``ATL-41``. Accepted anywhere the task's id is."""
        return f"{self.project.key}-{self.number}"


class TaskWaitingOn(Base, TimestampMixin):
    """Who a stalled task is waiting on, right now.

    Rewritten on every status change and emptied when a task goes back to
    active, so it always answers "who is holding this up today". Who was ever
    tagged is recorded in the comments, where the reason sits beside it.
    """

    __tablename__ = "task_waiting_on"

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        primary_key=True,
    )
    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        primary_key=True,
    )


class TaskComment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "task_comment"
    __table_args__ = (Index("ix_task_comment_task_id_created_at", "task_id", "created_at"),)

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )

    author_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Null for what Cylist wrote itself, and for an agent that did not say
    whose behalf it was speaking on."""

    body: Mapped[str] = mapped_column(Text, nullable=False)

    kind: Mapped[CommentKind] = mapped_column(
        _enum(CommentKind, "comment_kind"), nullable=False, default=CommentKind.COMMENT
    )

    meta: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    """For a status change: ``{from, to, reason, tagged: [person_id, …]}``.
    Empty for a comment somebody typed."""

    author: Mapped[Person | None] = relationship(lazy="selectin")
