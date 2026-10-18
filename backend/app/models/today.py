"""A person's plan for a day: the cards they picked up, and their to-dos.

Today used to be a list worked out from due dates. It is now something a
person writes: they pick cards off the board for a day, and under each one
they list what they mean to do about it — send an email, talk to somebody,
read a file. At the end of the day whatever is left either moves on to
another day or stays behind as not done.

Three choices are worth stating outright:

* **A plan is a person's own.** Nobody else's picks or to-dos come back from
  the API, and nothing here is written to the audit trail. The board is shared;
  how one person means to spend their Friday is not a change to it.
* **A to-do hangs off a pick, never off a card directly.** "This card is on my
  Friday" is a fact of its own (:class:`DayPick`), and it can be true with no
  to-dos under it yet. Taking the card off the day takes its to-dos with it.
* **Tags are in the text.** A to-do is written with the same ``@person`` and
  ``>file`` tags a comment is, and recognised the same way when it is read —
  see ``frontend/src/components/mentions.ts``. Nothing beside the text says
  who or what was meant, so a to-do reads the same in the browser, in the
  Markdown report and through an agent.

Moving a to-do on is a copy, not an edit: the original stays on its day as
``moved`` (with ``moved_to`` saying where), and the copy starts ``open`` on the
new day with ``carried_from`` saying where it came from. Each day's record is
then what that day actually held, which is what its report is read from.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, Date, DateTime, Enum, ForeignKey, Index, Integer, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.task import Task

TEXT_MAX_LENGTH = 500
"""How long one to-do may be. A line of work, not a description."""


class TodoState(StrEnum):
    """Where a to-do stands."""

    OPEN = "open"
    """Still to do. Where every to-do starts, including a carried one."""

    DONE = "done"

    MOVED = "moved"
    """Handed on to another day at wrap-up; the copy there is the live one."""

    NOT_DONE = "not_done"
    """Left behind when its day was wrapped up. It can still be reopened."""


class DayPick(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One card on one person's plan for one day."""

    __tablename__ = "day_pick"
    __table_args__ = (
        Index("ix_day_pick_person_id_task_id_day", "person_id", "task_id", "day", unique=True),
        Index("ix_day_pick_person_id_day", "person_id", "day"),
    )

    person_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
        nullable=False,
    )
    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    day: Mapped[date] = mapped_column(Date, nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Order on the day: the order the cards were picked in, until rearranged."""

    task: Mapped[Task] = relationship(lazy="joined")
    todos: Mapped[list[Todo]] = relationship(
        back_populates="pick",
        order_by="Todo.position",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )


class Todo(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One thing a person means to do about a card on a day."""

    __tablename__ = "todo"
    __table_args__ = (
        CheckConstraint("char_length(text) > 0", name="text_not_empty"),
        # Moved and the day it moved to are one fact, stated twice.
        CheckConstraint("(state = 'moved') = (moved_to IS NOT NULL)", name="moved_says_where"),
    )

    pick_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("day_pick.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    state: Mapped[TodoState] = mapped_column(
        Enum(
            TodoState,
            name="todo_state",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda states: [state.value for state in states],
        ),
        nullable=False,
        default=TodoState.OPEN,
    )
    done_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    moved_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The day this was handed on to, when it was."""
    carried_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    """The day this was handed on from, when it was — the day before it moved,
    not the day it was first written, because "from Thu" is what is useful to
    read on a Friday."""

    pick: Mapped[DayPick] = relationship(back_populates="todos")
