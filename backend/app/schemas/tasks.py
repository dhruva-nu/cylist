"""Tasks, their status changes, their timeline and their history."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.task import ChecklistState, CommentKind, TaskPriority, TaskStatus, TaskType
from app.schemas.activity import HistoryEntry
from app.schemas.agent_sessions import AgentPresence, AgentSessionRead
from app.schemas.common import Schema
from app.schemas.people import PersonRead


def _blank_to_none(value: str | None) -> str | None:
    """Treat an empty box in a form as "not set" rather than as an empty ref."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _clean_sub_statuses(value: list[str]) -> list[str]:
    """Strip each label and refuse a blank one — a stage with no name is
    nothing to advance towards."""
    cleaned = [label.strip() for label in value]
    if any(not label for label in cleaned):
        raise ValueError("a sub-status label must not be blank")
    return cleaned


class ColumnDueDateInput(Schema):
    """A date this card is wanted in one column by."""

    column_id: UUID = Field(
        description=(
            "One of the project's columns, and not the last one: a card is done "
            "when it reaches the end of the board, so the date for the last "
            "column is the card's own `due_date`."
        )
    )
    due_date: date


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


def _one_date_per_column(value: list[ColumnDueDateInput]) -> list[ColumnDueDateInput]:
    """Refuse a column named twice: two dates for one column is two answers."""
    named = [entry.column_id for entry in value]
    if len(named) != len(set(named)):
        raise ValueError("a column can only be given one due date")
    return value


class TaskCreate(Schema):
    """A new card. It lands in the board's first column.

    The one exception is a card whose template does not allow that column,
    which lands in the leftmost one it does allow instead — a card has to be
    born somewhere its own template permits.
    """

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(
        min_length=1, max_length=5000, description="What done looks like. Required."
    )
    type: TaskType
    priority: TaskPriority = Field(
        default=TaskPriority.P3, description="`p0` (drop everything) to `p3`. Defaults to `p3`."
    )
    sub_statuses: list[str] = Field(
        default_factory=list,
        max_length=4,
        description=(
            "Up to 4 short stage labels, left to right. Starts on the first one. "
            "Advancing through them happens on the board, not here. Overridden "
            "by `template_id`'s stage for the landing column, if it names one."
        ),
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "When it is wanted by — which is when it is wanted in the board's "
            "last column. Omit it — or send null — for a card with no date."
        ),
    )
    column_due_dates: list[ColumnDueDateInput] = Field(
        default_factory=list,
        description=(
            "Dates for the columns on the way there: the day it is wanted in "
            "Review, in QA. Any subset of the board's columns, the last one "
            "excepted — that one is `due_date`. Refused on a sub-task, which is "
            "not on the board."
        ),
    )
    assignee_id: UUID = Field(description="Must be a member of the project.")
    template_id: UUID | None = Field(
        default=None,
        description=(
            "What kind of card this is — one of the project's templates. Omit "
            "it for a card with no template, which may sit in any column and "
            "starts with whatever `sub_statuses` was sent. A card whose "
            "template is barred from the board's first column lands in the "
            "leftmost column its stages do allow, on the sub-stages that "
            "column's stage names, if any."
        ),
    )
    goal_id: UUID | None = Field(
        default=None,
        description=(
            "The goal this card is work towards — one of the project's goals. "
            "Omit it for a card that stands on its own, which most cards do. "
            "The goal's colour becomes the card's rail on the board."
        ),
    )
    jira_ref: str | None = Field(default=None, max_length=200)
    pr_ref: str | None = Field(default=None, max_length=200)

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("sub_statuses")
    @classmethod
    def _clean_sub_statuses(cls, value: list[str]) -> list[str]:
        return _clean_sub_statuses(value)

    @field_validator("column_due_dates")
    @classmethod
    def _one_per_column(cls, value: list[ColumnDueDateInput]) -> list[ColumnDueDateInput]:
        return _one_date_per_column(value)

    @field_validator("jira_ref", "pr_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class SubtaskCreate(TaskCreate):
    """A sub-task with a reference of its own, numbered under its parent.

    Same fields as any other task — an owner, a description, a due date, a
    priority — because it is one in every respect but placement: a sub-task is
    work on a card rather than a card on the board, so it lands in no column
    and is finished by being ticked off rather than by being moved.

    ``template_id`` is still accepted: what kind of work it is stays true. Its
    stages, which are rules about columns, simply have nothing to say here.
    """


class ChecklistItemCreate(Schema):
    """A sub-task that is a tick box: a line of text and nothing else."""

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ChecklistItemUpdate(Schema):
    """Retitle an item, or tick, cancel or reopen it. Both fields optional."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    state: ChecklistState | None = None

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class ChecklistItemRead(Schema):
    id: UUID
    task_id: UUID
    title: str
    state: ChecklistState
    position: int
    created_at: datetime


class TaskUpdate(Schema):
    """Every field optional; omitted fields are left as they are.

    Status is not here: it moves through ``POST /tasks/{ref}/status``, which is
    the only path that can demand a reason and record one.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    type: TaskType | None = None
    priority: TaskPriority | None = None
    sub_statuses: list[str] | None = Field(
        default=None,
        max_length=4,
        description=(
            "Replaces the whole list of stage labels. The current stage is kept "
            "if it still fits, otherwise pulled back to the new last one."
        ),
    )
    sub_status_index: int | None = Field(
        default=None,
        ge=0,
        description=(
            "Which stage is current, once `sub_statuses` has been applied. Send it "
            "when reordering or removing stages has moved the marker — the caller "
            "doing the reordering is the only one that knows where it went. Left "
            "out, the marker stays where it was."
        ),
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "A new date, or null to take the date off the card. Unlike the other "
            "fields here, null means clear rather than leave alone."
        ),
    )
    column_due_dates: list[ColumnDueDateInput] | None = Field(
        default=None,
        description=(
            "Replaces every per-column date the card has: send the whole set, "
            "an empty list to take them all off, or leave it out to leave them "
            "alone. A list rather than a patch per column because that is how "
            "the dates are read — as one schedule across the board."
        ),
    )
    assignee_id: UUID | None = None
    template_id: UUID | None = Field(
        default=None,
        description=(
            "A different template, or null to take the card out of one "
            "entirely. Like `due_date`, null here means clear rather than "
            "leave alone. Refused if the card's current column is one the new "
            "template does not allow — move it first. The card's current "
            "`sub_statuses` are left as they are; a new template's stages are "
            "not retroactively applied, only picked up the next time the card "
            "lands somewhere new."
        ),
    )
    goal_id: UUID | None = Field(
        default=None,
        description=(
            "A different goal, or null to unlink the card from the one it is "
            "on. Like `due_date`, null here means clear rather than leave "
            "alone. Refused on a sub-task: a sub-task belongs to its card, and "
            "its card is what belongs to a goal."
        ),
    )
    jira_ref: str | None = Field(default=None, max_length=200)
    pr_ref: str | None = Field(default=None, max_length=200)

    @field_validator("sub_statuses")
    @classmethod
    def _clean_sub_statuses(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_sub_statuses(value)

    @field_validator("column_due_dates")
    @classmethod
    def _one_per_column(
        cls, value: list[ColumnDueDateInput] | None
    ) -> list[ColumnDueDateInput] | None:
        return None if value is None else _one_date_per_column(value)

    @field_validator("jira_ref", "pr_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class TaskMove(Schema):
    """Puts a card in a column at a position.

    Any column at any time, unless the card's template has stages — then,
    any column one of its stages names. Leaving a column is itself refused
    until the card is on the last sub-stage its template set for that column.

    A sub-task cannot be moved at all: it is not on the board.
    """

    column_id: UUID
    position: int = Field(default=0, ge=0, description="Clamped to the column's length.")
    outcome: str | None = Field(
        default=None,
        description=(
            "Which of the column's outcomes the card lands on, by name — 'Done', 'Cancelled', "
            "'In prod'. Only the board's last column has any, and only if the board was "
            "divided that way. Left out, a card arriving there lands on the first one; "
            "moving anywhere else clears the card's outcome whatever this says."
        ),
    )


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


class TaskStatusChange(Schema):
    """Moves a task between active, on hold and blocked."""

    status: TaskStatus
    reason: str | None = Field(
        default=None,
        max_length=2000,
        description="Required for `hold` and `blocked`. Optional when going back to `active`.",
    )
    waiting_on: list[UUID] = Field(
        default_factory=list,
        description=(
            "Project members this is now waiting on. Ignored — and cleared — "
            "when the status goes back to `active`."
        ),
    )

    @field_validator("waiting_on")
    @classmethod
    def _deduplicate(cls, value: list[UUID]) -> list[UUID]:
        seen: dict[UUID, None] = dict.fromkeys(value)
        return list(seen)


class CommentCreate(Schema):
    body: str = Field(min_length=1, max_length=5000)
    author_id: UUID | None = Field(
        default=None,
        description="Who is speaking. Must be a project member. Omit for an agent.",
    )

    @field_validator("body")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


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


class TaskRead(Schema):
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
        description="Which column the card is in. Null on a sub-task, which is not on the board."
    )
    position: int | None = Field(
        description="Where the card sits in its column, from the top. Null on a sub-task."
    )
    title: str
    description: str
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
    pr_ref: str | None
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
        "it needs a human; `working` is live; `done` is finished and not yet dismissed; "
        "`stale` is a working session nobody has heard from. Null when no agent is on it.",
    )
    created_at: datetime


class TaskDetail(TaskRead):
    """One task with its timeline, its sub-tasks and its checklist."""

    comments: list[CommentRead]
    agent_sessions: list[AgentSessionRead] = Field(
        default_factory=list,
        description="Every harness session on this card still worth showing: the open ones "
        "first, then the finished ones nobody has dismissed.",
    )
    subtasks: list[TaskRead] = Field(
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
