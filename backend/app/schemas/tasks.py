"""Tasks, their status changes, their timeline and their history."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field

from app.models.task import (
    ChecklistState,
    CommentKind,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from app.schemas.activity import HistoryEntry
from app.schemas.agent_sessions import AgentPresence, AgentSessionRead
from app.schemas.common import Page, Schema
from app.schemas.files import ItemRead
from app.schemas.people import PersonRead
from app.services.commands.tasks import (
    ChecklistItemCreate as ChecklistItemCreate,
)
from app.services.commands.tasks import (
    ChecklistItemUpdate as ChecklistItemUpdate,
)
from app.services.commands.tasks import (
    ColumnDueDateInput as ColumnDueDateInput,
)
from app.services.commands.tasks import (
    CommentCreate as CommentCreate,
)
from app.services.commands.tasks import (
    PrRef as PrRef,
)
from app.services.commands.tasks import (
    TaskCreate as TaskCreate,
)
from app.services.commands.tasks import (
    TaskMove as TaskMove,
)
from app.services.commands.tasks import (
    TaskStatusChange as TaskStatusChange,
)
from app.services.commands.tasks import (
    TaskUpdate as TaskUpdate,
)
from app.services.commands.tasks import (
    _blank_to_none as _blank_to_none,
)
from app.services.commands.tasks import (
    _clean_pr_refs as _clean_pr_refs,
)
from app.services.commands.tasks import (
    _clean_sub_statuses as _clean_sub_statuses,
)
from app.services.commands.tasks import (
    _one_date_per_column as _one_date_per_column,
)

COMMENTS_ON_A_CARD = 50
"""How much of its timeline a card carries. The newest entries, because a card
is opened to see where it has got to; the rest is a page away."""


class ColumnDueDateRead(ColumnDueDateInput):
    """One of those dates, with what a reader needs to make sense of it."""

    column_name: str = Field(description="That column's name, so a card reads alone.")
    met: bool = Field(
        description=(
            "Whether the card has reached that column. A met date is behind the "
            "card and stops being asked anything of — it never reads as late, "
            "and `next_due_date` skips it."
        )
    )


class SubtaskCreate(TaskCreate):
    """A sub-task with a reference of its own, numbered under its parent.

    Same fields as any other task — an owner, a description, a due date, a
    priority — because it is one in every respect but placement: a sub-task is
    work on a card rather than a card on the board, so it lands in no column
    and is finished by being ticked off rather than by being moved.

    ``template_id`` is still accepted: what kind of work it is stays true. Its
    stages, which are rules about columns, simply have nothing to say here.
    """


class ChecklistItemRead(Schema):
    id: UUID
    task_id: UUID
    title: str
    state: ChecklistState
    position: int
    created_at: datetime


class TaskFinish(Schema):
    """Ticks a sub-task off, or puts it back."""

    finished: bool = Field(
        default=True,
        description="`true` finishes the sub-task, `false` reopens it. Finishing one already "
        "finished changes nothing and does not restate the time.",
    )


class SubStatusMove(Schema):
    """Moves a card to one of its own sub-status stages."""

    index: int = Field(
        ge=0,
        description=(
            "Which stage to move to, counting from 0. Any of them, in either "
            "direction — not just the next one."
        ),
    )


class CommentRead(Schema):
    id: UUID
    task_id: UUID
    author: PersonRead | None
    body: str
    kind: CommentKind
    meta: dict[str, Any] = Field(
        description="For a `status_change`: `{from, to, reason, tagged}`. Empty otherwise."
    )
    created_at: datetime


class TaskSummary(Schema):
    """A card as a list draws it: everything but its description.

    The description is the one unbounded field on a card — up to 5000
    characters — and no list shows it, so no list carries it (CYLIST-130).
    `GET /tasks/{ref}` is where it is read, as part of :class:`TaskDetail`.
    """

    id: UUID
    project_id: UUID
    reference: str = Field(
        description="`ATL-41`, or `ATL-41-2` for a sub-task. Usable in place of the id."
    )
    number: int | None = Field(
        description="Per-project card number. Null on a sub-task, which is numbered "
        "under its parent."
    )
    parent_id: UUID | None = Field(description="The card this was split out of, if any.")
    parent_reference: str | None = Field(description="`ATL-41`, when this is a sub-task.")
    sub_number: int | None = Field(
        description="Position in the parent's numbering. `2` in `ATL-41-2`."
    )
    column_id: UUID | None = Field(
        description="Which column the card is in. Null on a sub-task, which is not on the board, "
        "and on a card in cold storage, which has left it."
    )
    position: int | None = Field(
        description="Where the card sits in its column: sort ascending for top to bottom. A "
        "sort key, not an index — it has gaps and may be negative. Null on a sub-task and on "
        "a card in cold storage."
    )
    title: str
    type: TaskType
    priority: TaskPriority
    sub_statuses: list[str] = Field(
        description="Up to 4 stage labels, left to right. Empty if the card doesn't use this."
    )
    sub_status_index: int | None = Field(
        description="Index into `sub_statuses` of the current stage. Null when the list is empty."
    )
    due_date: date | None = Field(
        description="When the card is wanted in the board's last column. Null when it has no date."
    )
    column_due_dates: list[ColumnDueDateRead] = Field(
        description="Dates for the columns before the last one, in board order. Empty on a "
        "card nobody has dated a stage of, and always empty on a sub-task."
    )
    next_due_date: date | None = Field(
        description="The date the card is working towards now: the soonest of its unmet dates, "
        "`due_date` among them. Null once the card is done — in the last column, or ticked off "
        "if it is a sub-task — and null when it is dated nowhere. This is the date to draw on a "
        "card: `due_date` is the end of the line, this is the next thing owed."
    )
    assignee: PersonRead
    status: TaskStatus
    template_id: UUID | None = Field(
        description="The template this card was created from, if any. Null is unrestricted."
    )
    template_name: str | None = Field(description="That template's name, so a card reads alone.")
    goal_id: UUID | None = Field(
        description="The goal this card is work towards, if any. Null stands on its own."
    )
    goal_reference: str | None = Field(description="`ATL-G1`, when the card is on a goal.")
    goal_name: str | None = Field(description="That goal's name, so a card reads alone.")
    goal_colour: str | None = Field(
        description="That goal's six-digit hex — the rail the board draws down the card. Null "
        "when the card is on no goal, and the board draws its status colour instead."
    )
    jira_ref: str | None
    pr_refs: list[PrRef] = Field(
        description="The pull requests this card's work lands as, in the order they were "
        "added, each with how far it got. Empty on a card that has none yet, which most cards "
        "are for most of their lives."
    )
    waiting_on: list[PersonRead] = Field(
        description="Who this is waiting on. Empty unless the task is on hold or blocked."
    )
    comment_count: int
    checklist: list[ChecklistItemRead] = Field(
        description="Tick-box sub-tasks. Every one must be done or cancelled before the card "
        "can reach the board's last column."
    )
    outcome: str | None = Field(
        description="How the work ended: the section of the board's last column this card is "
        "in, by name. Null on every card that is not in a column divided that way."
    )
    outcome_index: int | None = Field(
        description="Which of the column's `outcomes` that is, counted from the left. Null "
        "exactly when `outcome` is."
    )
    finished_at: datetime | None = Field(
        description="When this task was finished, and null while it is open. A sub-task is "
        "finished by being ticked off; a card is finished by being moved into the board's last "
        "column, and moving it back out clears this."
    )
    archived_at: datetime | None = Field(
        default=None,
        description="When this card was put in cold storage, or null while it is on the board. "
        "A stored card is off the board but still finished, and `POST /tasks/{ref}/restore` puts "
        "it back in the board's last column.",
    )
    open_subtask_count: int = Field(
        description="Sub-tasks — cards and tick boxes together — that are neither finished nor "
        "cancelled. While this is above zero the card cannot reach the last column."
    )
    subtask_count: int = Field(
        description="How many sub-tasks the card has in all, cancelled ones excluded. With "
        "`open_subtask_count` this is the progress a card shows: `total - open` are done."
    )
    subtask_assignees: list[PersonRead] = Field(
        description="Who owns this card's sub-tasks, in sub-number order and each named once. "
        "The board shows these faces because it no longer shows where the sub-tasks are."
    )
    agent_session: AgentPresence | None = Field(
        default=None,
        description="Who is working on this card right now, if an agent is: the one state "
        "the card's border shows, reduced from every harness session on it. `waiting` means "
        "it needs a human; `working` is live; `done` is finished and not yet dismissed — "
        "including a session whose connection was lost, whose `reason` says so. Null when "
        "no agent is on it.",
    )
    created_at: datetime


class TaskSummaryPage(Page[TaskSummary]):
    """A page of cards: cold storage, a goal's cards, a column of the board."""


class ColumnTasks(Schema):
    """The first page of one column of the board, and how many it holds in all."""

    column_id: UUID
    total: int = Field(description="Cards in the column in all, not only on this page.")
    items: list[TaskSummary] = Field(description="The column's cards, top to bottom.")
    next: UUID | None = Field(
        description="Pass as `after` to `GET /columns/{column_id}/tasks` for the cards below "
        "these. Null when the column is all here."
    )


class BoardTasks(Schema):
    """A board's cards, a page per column."""

    columns: list[ColumnTasks] = Field(
        description="One per column, left to right — empty columns included, so a client can "
        "draw the board from this alone."
    )


class CommentPage(Page[CommentRead]):
    """A page of a card's timeline, newest first, and how long the whole timeline is."""

    total: int = Field(description="How many entries the whole timeline holds.")


class TaskDetail(TaskSummary):
    """One task with its description, its latest timeline, its sub-tasks and its checklist."""

    description: str
    comments: list[CommentRead] = Field(
        description="The newest entries of the timeline, oldest first — the last "
        f"{COMMENTS_ON_A_CARD} at most. `comment_count` says how many there are in all."
    )
    comments_next: UUID | None = Field(
        description="Pass as `after` to `GET /tasks/{ref}/comments` for the entries before "
        "these. Null when `comments` is the whole timeline."
    )
    agent_sessions: list[AgentSessionRead] = Field(
        default_factory=list,
        description="Every harness session on this card still worth showing: the open ones "
        "first, then the finished ones nobody has dismissed.",
    )
    subtasks: list[TaskSummary] = Field(
        description="Sub-tasks with a reference of their own, in sub-number order. They are "
        "not on the board, so this is the only place they are listed."
    )


class TaskHistoryPage(Schema):
    """One page of a task's history, and enough to ask for the next.

    An envelope rather than a bare list because a page of ten is only useful
    beside the number it is ten of: without ``pages`` a client cannot draw a
    pager, and without ``total`` it cannot say whether it is showing all of a
    short history or the tip of a long one.

    The entries are plain :class:`~app.schemas.activity.HistoryEntry` rows: a
    card's history and a day's report are the same trail read two ways, and a
    task adds nothing of its own to an entry of it.
    """

    entries: list[HistoryEntry]
    total: int = Field(description="How many entries the whole history holds.")
    page: int = Field(description="Which page this is, counting from 1.")
    pages: int = Field(description="How many pages there are. At least 1, even when empty.")
    per_page: int = Field(description="How many entries a page holds.")


class ColdStorageRun(Schema):
    """What putting a board's finished cards in cold storage did."""

    column: str = Field(description="The column the cards were taken from: the board's last.")
    references: list[str] = Field(
        description="The cards stored, top to bottom as they stood in the column.",
        examples=[["ATL-12", "ATL-17"]],
    )
    file: ItemRead = Field(
        description="The CSV they were written to, in the project's `Cold storage` folder."
    )
