"""Goals: the epic a card belongs to.

A goal is an outcome the board is working towards — "Search revamp", "SOC 2
readiness" — with cards linked to it and a colour of its own. It is deliberately
*not* a card: it has no column, no position, no template and no place on the
board. A goal is what the work is for; a card is the work.

Three choices are worth stating outright:

* **A card belongs to at most one goal.** The board draws a goal by colouring
  the card's rail, and a rail can only be one colour; more than that, "what is
  left on this goal" has one honest answer only if each card is counted once.
* **A goal is set on a card, not on a sub-task.** A sub-task belongs to its
  card and its card belongs to the goal — see the ``goal_only_on_cards``
  constraint, which makes that a schema fact rather than a convention.
* **A goal is named once per project**, like a template, because a card wears
  its goal by colour and by name and two "Search revamp"s make both ambiguous.

Deleting a goal does not delete its cards — ``ON DELETE SET NULL``. A template
is a rule about where its cards may go, so a card cannot lose one out from
under it; a goal is a label saying what the card is for, and a label can be
taken off.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import Person
from app.models.project import Project

NAME_MAX_LENGTH = 120


class GoalStatus(StrEnum):
    """Whether a goal is still being worked towards, and if not, how it ended."""

    OPEN = "open"
    """Live. The default, and the only state a goal can be created in."""

    ACHIEVED = "achieved"
    """Reached. Cannot be set while a linked card is still open — see
    :func:`app.services.goals.set_status`, which names what is outstanding
    rather than letting a goal close over live work."""

    DROPPED = "dropped"
    """Given up on. The way a goal that is not going to happen stops appearing
    in the picker, without taking its cards or its history with it — the same
    role ``cancelled`` plays for a task."""

    @property
    def label(self) -> str:
        return _STATUS_LABELS[self]

    @property
    def is_settled(self) -> bool:
        """Whether this goal is done being worked on, either way."""
        return self is not GoalStatus.OPEN


_STATUS_LABELS = {
    GoalStatus.OPEN: "Open",
    GoalStatus.ACHIEVED: "Achieved",
    GoalStatus.DROPPED: "Dropped",
}


class Goal(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "goal"
    __table_args__ = (
        Index("ix_goal_project_id_number", "project_id", "number", unique=True),
        # Unique on the project, for the reason a template's name is: a goal is
        # read off a card by name and by colour, and two goals sharing a name
        # make both of those a guess.
        Index("ix_goal_project_id_name", "project_id", "name", unique=True),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    number: Mapped[int] = mapped_column(Integer, nullable=False)
    """Per-project, handed out by ``project.goal_counter`` and never reused.

    With the project's key it forms ``ATL-G1``, which is what a goal is
    addressed by — a reference rather than a name because a goal gets renamed
    ("Search revamp" becomes "Search and filters") and the URL somebody
    bookmarked should still open it. The ``G`` is what keeps it out of the
    task numbering: ``ATL-1`` is a card, ``ATL-G1`` is a goal, and neither can
    be misread as the other.
    """

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """What reaching this goal means. Optional, unlike a card's: a card without
    one is work nobody else can pick up, while a goal is a heading its cards
    spell out underneath."""

    colour: Mapped[str] = mapped_column(String(7), nullable=False)
    """Six-digit hex. Drawn as the rail down the left of every card linked to
    this goal, which is the whole point of it being here: a column of cards
    shows which goals it is made of without a word being read."""

    status: Mapped[GoalStatus] = mapped_column(
        Enum(
            GoalStatus,
            name="goal_status",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
        default=GoalStatus.OPEN,
        server_default=sql_text("'open'"),
    )

    achieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When the goal was reached, or null while it is open or dropped.

    A timestamp rather than a flag for the reason ``Task.finished_at`` is one:
    the question asked of a goal that is done is nearly always *when*.
    """

    target_date: Mapped[date | None] = mapped_column(Date)
    """When the goal is wanted by, or null when nobody has said.

    Optional for the reason ``Task.due_date`` is: a date invented to get past a
    form makes the goal late on a day nobody chose.
    """

    owner_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id"),
        nullable=False,
    )
    """Required, and always a member of the goal's project. An epic nobody is
    named on is the one thing on a board that can drift for a quarter without
    anybody noticing it has."""

    project: Mapped[Project] = relationship(lazy="selectin")
    owner: Mapped[Person] = relationship(lazy="selectin")

    @property
    def reference(self) -> str:
        """``ATL-G1``. Accepted anywhere the goal's id is."""
        return f"{self.project.key}-G{self.number}"

    @property
    def is_settled(self) -> bool:
        return self.status.is_settled
