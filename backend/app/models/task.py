"""Tasks: the cards, who a stalled one is waiting on, and their timeline.

A task's whole history — comments people wrote and status changes Cylist
recorded — lives in one table, so a card reads as a single story rather than
two lists the reader has to interleave by timestamp.

One rule shapes half the columns below: **a sub-task is work on a card, not a
card on the board.** It keeps its reference, its owner, its due date, its
comments and its history; what it does not have is a column. So ``column_id``
and ``position`` belong to top-level cards alone, which is a check constraint
rather than a convention, because a row that is half on the board and half off
it is not a state any code here knows how to read.

``finished_at`` is the one field the two kinds share, arrived at from different
directions: a sub-task is finished by being ticked off, a card by being moved
into the board's last column. Both write the same timestamp, so *when did this
stop being work* is one question with one answer wherever it is asked.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.board import BoardColumn
from app.models.goal import Goal
from app.models.person import Person
from app.models.project import Project
from app.models.template import TaskTemplate


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

    CANCELLED = "cancelled"
    """Dropped. Not going to be done, and nothing is waiting for it.

    A settled state rather than a stalled one: a cancelled sub-task no longer
    holds its parent back, which is the whole reason the state exists.

    The other way to stop holding a parent back is to be finished, which is
    ``finished_at`` rather than a member here: this enum answers "is it moving,
    and if not, why not", and *done* is not a reason for not moving.
    """

    @property
    def label(self) -> str:
        """How this status is worded in a status-change comment."""
        return _STATUS_LABELS[self]


_STATUS_LABELS = {
    TaskStatus.ACTIVE: "Active",
    TaskStatus.HOLD: "On hold",
    TaskStatus.BLOCKED: "Blocked",
    TaskStatus.CANCELLED: "Cancelled",
}


class TaskPriority(StrEnum):
    """How soon this needs attention, P0 (most) to P3 (least).

    Named by level rather than by feeling: "asap" and "this week" are two
    people's words for the same urgency, and a scale everybody already reads
    the same way needs no glossary.
    """

    P0 = "p0"
    """Drop what you are doing."""

    P1 = "p1"
    """As fast as possible, once whatever is P0 is out of the way."""

    P2 = "p2"
    """Some time in the next week."""

    P3 = "p3"
    """Some time in the future. The default: nothing is marked urgent by not
    having been asked about yet."""


class ChecklistState(StrEnum):
    """Where one checklist item has got to.

    Only three states, and two of them are settled: a checklist exists to be
    emptied, and an item nobody will ever tick is cancelled rather than left
    open forever holding the card back.
    """

    OPEN = "open"
    DONE = "done"
    CANCELLED = "cancelled"

    @property
    def is_settled(self) -> bool:
        """Whether this item has stopped holding its task back."""
        return self is not ChecklistState.OPEN


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
        # Unique over top-level cards alone: a sub-task has no project number,
        # it borrows its parent's and adds its own to the end.
        Index(
            "ix_task_project_id_number",
            "project_id",
            "number",
            unique=True,
            postgresql_where=sql_text("parent_id IS NULL"),
        ),
        Index("ix_task_project_id_column_id_position", "project_id", "column_id", "position"),
        Index("ix_task_parent_id_sub_number", "parent_id", "sub_number", unique=True),
        # Exactly one of the two numbering schemes applies to any given row, so
        # neither "a top-level card with a sub-number" nor "a sub-task with a
        # project number" can be written at all.
        CheckConstraint(
            "(parent_id IS NULL) = (number IS NOT NULL) "
            "AND (parent_id IS NULL) = (sub_number IS NULL)",
            name="numbered_by_parentage",
        ),
        # The same shape, for the same kind of reason: a top-level card is on
        # the board and always in a column, a sub-task is not on the board and
        # never in one. Neither "a card in no column" nor "a sub-task in Review"
        # can be written at all.
        CheckConstraint(
            "(parent_id IS NULL) = (column_id IS NOT NULL) "
            "AND (parent_id IS NULL) = (position IS NOT NULL)",
            name="placed_by_parentage",
        ),
        # A card can be split into at most 4 stages — enough to read as a
        # position along a bar, not so many that a segment on a board card is
        # too narrow to point at.
        CheckConstraint("cardinality(sub_statuses) <= 4", name="sub_status_max_four"),
        # The pointer exists exactly when there is something for it to point
        # at: no stage list means nothing is "current", and any stage list has
        # exactly one.
        CheckConstraint(
            "(sub_status_index IS NULL) = (cardinality(sub_statuses) = 0)",
            name="sub_status_index_matches_list",
        ),
        CheckConstraint(
            "sub_status_index IS NULL "
            "OR (sub_status_index >= 0 AND sub_status_index < cardinality(sub_statuses))",
            name="sub_status_index_in_range",
        ),
        # A goal is what a card is for, and a sub-task's card already answers
        # that. Letting a sub-task name a goal of its own would put a second
        # answer under the first, and let the two disagree.
        CheckConstraint(
            "goal_id IS NULL OR parent_id IS NULL",
            name="goal_only_on_cards",
        ),
        # An outcome is a section of a column, so only something in a column
        # can be in one. Which section is a valid one is a question about the
        # column's own list and is answered in the service — see
        # ``Task.outcome_index``.
        CheckConstraint(
            "outcome_index IS NULL OR (parent_id IS NULL AND outcome_index >= 0)",
            name="outcome_only_on_cards",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    number: Mapped[int | None] = mapped_column(Integer)
    """Per-project, handed out by ``project.task_counter`` and never reused.
    Null on a sub-task, which is numbered under its parent instead.
    With the project's key it forms the reference the task is known by
    everywhere else — ``ATL-41`` in a commit message, a Slack thread, a CLI
    argument — so the same number turning up on a second task would be a lie."""

    column_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("board_column.id"),
    )
    """Which column the card is in. Null on a sub-task, which is not on the
    board at all — see ``placed_by_parentage``.

    No ``ondelete``: NO ACTION is checked at the end of the statement, so
    deleting a project still cascades cleanly, while deleting a column that
    holds tasks fails rather than quietly taking the tasks with it."""

    position: Mapped[int | None] = mapped_column(Integer)
    """Top to bottom within the column, contiguous from zero. Null on a
    sub-task, which is ordered by ``sub_number`` under its parent instead."""

    outcome_index: Mapped[int | None] = mapped_column(Integer)
    """Which of the column's ``outcomes`` this card ended on, or null.

    Null on everything not in a column that draws the distinction, which is
    every card on most boards: outcomes belong to the board's last column
    alone. Set when a card arrives there and cleared when it leaves — see
    :func:`app.services.tasks.move`.

    An index rather than the label, so renaming "In prod" to "Released"
    renames it on every card that landed there rather than stranding them under
    a heading that no longer exists. The cost is that shortening the list has
    to say what becomes of the cards past its end, which
    :func:`app.services.columns.update` does by moving them to the last section
    still standing.

    Orthogonal to ``finished_at`` and to ``status``, which is the point of
    having it: *when* the work stopped, *whether* it is moving, and *how* it
    ended are three questions, and a board that had only the first two had to
    answer the third by adding a column."""

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    """Required: a card whose title is the whole story is a card nobody else
    can pick up."""

    type: Mapped[TaskType] = mapped_column(_enum(TaskType, "task_type"), nullable=False)

    priority: Mapped[TaskPriority] = mapped_column(
        _enum(TaskPriority, "task_priority"),
        nullable=False,
        default=TaskPriority.P3,
        server_default=sql_text("'p3'"),
    )

    sub_statuses: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(60)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'"),
    )
    """Up to 4 stage labels, left to right — a progress bar within a column
    rather than the column itself, which is why :func:`app.services.tasks.move`
    starts them again when a card changes column. Empty means the card does not
    use the feature at all. Long labels are fine: the board draws position, not
    words, and shows the words on hover."""

    sub_status_index: Mapped[int | None] = mapped_column(Integer)
    """Which of ``sub_statuses`` is current — the stage in hand, not one
    finished. Null exactly when the list is empty — see
    ``sub_status_index_matches_list``. Moved by
    :func:`app.services.tasks.set_sub_status` (the board card's slider), reset
    to the first stage by :func:`app.services.tasks.move` when the card changes
    column, and kept in range by the service whenever ``sub_statuses`` is
    edited out from under it."""

    due_date: Mapped[date | None] = mapped_column(Date)
    """When the work is wanted by, or null when nobody has said.

    The date for the end of the board: a card is done when it reaches the last
    column, so the day it is wanted done is the day it is wanted there. Dates
    for the columns before that one are :class:`TaskColumnDueDate` rows.

    Optional because a date invented to get past a form is worse than no date
    at all: it makes the card overdue on a day nobody chose, and an overdue
    marker that fires on a guess is one the board learns to ignore. A card
    without one is simply not dated — it never reads as late."""

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

    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When this task was finished, or null while it is still open.

    Set two ways, and cleared the same two: a sub-task is ticked off, and a card
    is moved into the board's last column — see
    :func:`app.services.tasks.set_finished` and :func:`app.services.tasks.move`.
    Not a constraint, because the rule is about a board the row cannot see: no
    column knows it is the last one, and the position that makes it so changes
    the moment a column is added to its right.

    Which is also why the field is worth writing at all rather than being read
    off the board every time it is asked. A card that was done in June and was
    moved back out in July was still done in June, and the day's report, a
    goal's progress and a card's own history all have to be able to say so.

    A timestamp rather than a flag because the question asked of a settled task
    is nearly always *when*: the day's report wants it, and a boolean that has
    to be joined against the activity trail to answer it is a boolean that was
    the wrong shape."""

    template_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task_template.id"),
    )
    """What kind of card this is, if it was created as one.

    Null on a card written before templates existed, and on one created
    without picking a template — which is unrestricted, not untyped: a
    template's stages say where its cards may go, and a card with none may
    go anywhere on its board.

    No ``ondelete``, for the reason ``column_id`` has none: NO ACTION is
    checked at the end of the statement, so a project still cascades away
    cleanly while a template a card is using cannot be deleted out from under
    it.
    """

    goal_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("goal.id", ondelete="SET NULL"),
    )
    """The goal this card is work towards, if any. Null is not "unclassified" —
    it is a card that stands on its own, which most cards do.

    ``ON DELETE SET NULL`` where ``template_id`` has no ``ondelete`` at all,
    because the two mean different things: a template is a rule about where a
    card may go and cannot be pulled out from under one, while a goal is a
    label saying what the card is for. A label can be taken off, and taking it
    off is not a reason to refuse to delete the goal.

    Only a card can carry one — see ``goal_only_on_cards``.
    """

    jira_ref: Mapped[str | None] = mapped_column(String(200))
    pr_ref: Mapped[str | None] = mapped_column(String(200))

    parent_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
    )
    """The card this one was split out of, if any.

    One level deep and no further: a sub-task that could itself be split turns
    the board into a tree, and a tree is a thing you navigate rather than read.
    Enforced in the service, which is the only place that can see the parent.
    """

    sub_number: Mapped[int | None] = mapped_column(Integer)
    """Per-parent, handed out by ``parent.subtask_counter``. Null at the top
    level. With the parent's reference it forms ``ATL-41-2``."""

    subtask_counter: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=sql_text("0")
    )
    """Highest sub-number ever handed out under this card. Only ever goes up,
    for the same reason ``project.task_counter`` does."""

    project: Mapped[Project] = relationship(lazy="selectin")
    assignee: Mapped[Person] = relationship(lazy="selectin")
    column: Mapped[BoardColumn | None] = relationship(lazy="selectin")
    """The column the card is in, loaded with it so ``outcome_index`` can be
    read back as the label it points at. Null on a sub-task, which is in
    none."""
    template: Mapped[TaskTemplate | None] = relationship(lazy="selectin")
    goal: Mapped[Goal | None] = relationship(lazy="selectin")

    waiting_on: Mapped[list[Person]] = relationship(
        secondary="task_waiting_on",
        order_by=Person.name,
        lazy="selectin",  # one extra query per fetch, never N+1
    )

    parent: Mapped[Task | None] = relationship(
        remote_side="Task.id",
        back_populates="subtasks",
        lazy="selectin",
        # Loaded eagerly because ``reference`` cannot be read without it, and a
        # reference is in every response a task appears in. The chain stops
        # after one hop: a parent never has a parent of its own.
        join_depth=2,
    )

    subtasks: Mapped[list[Task]] = relationship(
        back_populates="parent",
        cascade="all, delete-orphan",
        order_by="Task.sub_number",
        passive_deletes=True,  # the FK's ON DELETE CASCADE does the work
        lazy="noload",  # loaded deliberately, never on the board's list query
    )

    checklist: Mapped[list[TaskChecklistItem]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        order_by="TaskChecklistItem.position",
        passive_deletes=True,
        lazy="selectin",
    )

    column_due_dates: Mapped[list[TaskColumnDueDate]] = relationship(
        back_populates="task",
        cascade="all, delete-orphan",
        passive_deletes=True,
        lazy="selectin",
    )
    """The dates this card is wanted in particular columns by.

    Unordered here: they are read in board order, and the board's order is a
    fact about the columns rather than about this card."""

    @property
    def reference(self) -> str:
        """``ATL-41``, or ``ATL-41-2`` for a sub-task on the board.

        Accepted anywhere the task's id is.
        """
        if self.parent is not None:
            return f"{self.parent.reference}-{self.sub_number}"
        return f"{self.project.key}-{self.number}"

    @property
    def outcome(self) -> str | None:
        """How the work ended, as the label rather than the index.

        Null wherever the card is not in a section: no index, no column, or an
        index the column's list no longer reaches — which
        :func:`app.services.columns.update` settles as it happens, so the last
        of those is a belt-and-braces read rather than a state to expect.
        """
        if self.outcome_index is None or self.column is None:
            return None
        outcomes = self.column.outcomes
        return outcomes[self.outcome_index] if self.outcome_index < len(outcomes) else None

    @property
    def is_settled(self) -> bool:
        """Whether this task has stopped holding a parent back.

        Finished or cancelled — the two ways a sub-task stops being outstanding,
        and the only two states a parent's move to the last column is allowed to
        find under it.
        """
        return self.status is TaskStatus.CANCELLED or self.finished_at is not None


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


class TaskColumnDueDate(Base, TimestampMixin):
    """The day a card is wanted in one particular column by.

    A board's columns are stages the work passes through, and a card that has
    to be finished by the end of the month usually has to be in review well
    before that. Until now only the end of the line could be dated, so the
    intermediate deadlines lived in somebody's head.

    One row per column at most, and only for the columns somebody cared to
    date: a board of six columns with two deadlines on it has two rows here,
    not six with four nulls. The last column is not among them — a card is done
    when it reaches the end of the board, so the date for the end of the board
    is ``Task.due_date``, and a second place to write it would be a second
    answer that could disagree.

    Whether a date has been *met* is not stored either. It is where the card
    is: once the card has reached the column, the day it was wanted there is
    behind it, and a flag saying so would be a copy of the board that could
    fall out of step with it.
    """

    __tablename__ = "task_column_due_date"

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        primary_key=True,
    )

    column_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("board_column.id", ondelete="CASCADE"),
        primary_key=True,
    )
    """``ON DELETE CASCADE``, unlike ``Task.column_id``, which has no
    ``ondelete`` at all: a column holding cards must not be deleted out from
    under them, but a deadline for a column that no longer exists is a date
    with nothing to be due in."""

    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    """Not nullable: a row with no date is a row that should not be here."""

    task: Mapped[Task] = relationship(back_populates="column_due_dates")


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


class TaskChecklistItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A sub-task that is a tick box rather than a card.

    The lightweight half of CYLIST-3: no column, no assignee, no reference —
    just a line of text that has to be ticked or dropped before the card it
    sits on can reach the end of the board. Anything that needs an owner and a
    due date is a sub-task on the board instead.
    """

    __tablename__ = "task_checklist_item"
    __table_args__ = (Index("ix_task_checklist_item_task_id_position", "task_id", "position"),)

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )

    title: Mapped[str] = mapped_column(String(200), nullable=False)

    state: Mapped[ChecklistState] = mapped_column(
        _enum(ChecklistState, "checklist_state"), nullable=False, default=ChecklistState.OPEN
    )

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Top to bottom within the card, contiguous from zero."""

    task: Mapped[Task] = relationship(back_populates="checklist")
