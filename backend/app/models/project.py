"""Projects, and who is on them.

A project is the top-level container: it owns a board, a file tree, a vault and
a set of members. Everything else in Cylist hangs off one of these.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    text,
)
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

    task_counter: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    """Highest task number ever handed out on this board.

    A counter rather than ``MAX(task.number) + 1`` because deleting the newest
    task must not put its number back in circulation: ``ATL-41`` appears in
    commit messages and Jira long after the card it named is gone.
    """

    goal_counter: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    """Highest goal number ever handed out on this project, for ``ATL-G1``.

    Its own counter rather than a share of ``task_counter``: the two numberings
    are read side by side and would be unreadable interleaved — a board whose
    cards ran 1, 3, 4, 7 because the gaps were goals would look like a board
    with four deleted cards.
    """

    members: Mapped[list[Person]] = relationship(
        secondary="project_member",
        order_by=(KIND_ORDER, Person.name),
        lazy="selectin",  # one extra query per fetch, never N+1
    )

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None


class ProjectMember(Base, TimestampMixin):
    """Join table putting a person on a project, and saying what they are on it.

    Membership is what the assignee and "waiting on" pickers read, so adding
    someone to a project is what makes them selectable on its board.
    """

    __tablename__ = "project_member"
    __table_args__ = (
        # A member's role has to belong to the member's project. Pointing at
        # (project_id, id) rather than at project_role.id alone is what makes
        # that true in the database; the unique constraint on the far side
        # exists only to support this.
        #
        # NO ACTION rather than RESTRICT, and deferred, because of what
        # happens when a whole project goes: both this table and `project_role`
        # hang off `project` with ON DELETE CASCADE, and an immediate check
        # would refuse the delete or not depending on which of the two cascade
        # triggers Postgres happened to fire first. Deferred, both sets of rows
        # are gone by the time anybody looks. Deleting a role that is still
        # held is refused all the same — at COMMIT, and by
        # :func:`app.services.roles.delete` long before that, which can name
        # who is wearing it.
        ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_project_member_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

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

    role_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Which of the project's roles this person holds, or ``None``.

    Nullable because a role is something an admin gives you rather than
    something joining a project hands out: a new member has no role until
    somebody says what they are, and "no role yet" is an honest state that a
    seeded default would only have hidden. The board draws it as no badge.

    Constrained by the composite foreign key above rather than by a plain one
    — see the note there.
    """
