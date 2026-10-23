"""The commands the tasks services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.tasks``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field, field_validator

from app.models.task import (
    MAX_PR_REFS,
    REF_MAX_LENGTH,
    ChecklistState,
    PrState,
    TaskPriority,
    TaskStatus,
    TaskType,
)
from app.services.commands.base import Command
from app.services.commands.templates import SubStageLabel


def _blank_to_none(value: str | None) -> str | None:
    """Treat an empty box in a form as "not set" rather than as an empty ref."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class PrRef(Command):
    """One pull request carrying a card's work: where it is, and how far it got.

    The state is the whole of what this schema adds over the bare string the
    field used to hold, and it is optional on the way in because ``raised`` is
    where a pull request's life starts: somebody pasting a link has just opened
    it, and asking them to say so would be a question with one answer. On the
    way out it is always set — a stored entry has a state or it is not a stored
    entry.

    This is the only shape that reaches :attr:`app.models.task.Task.pr_refs`,
    which is why it is the thing holding what an entry may contain: the column
    is JSON, and a CHECK cannot reach inside a document without a subquery.
    """

    url: str = Field(
        max_length=REF_MAX_LENGTH,
        description=(
            "Where the pull request is — a URL, or a short form such as `#212`. "
            "Free text, as the Jira reference is: a URL is the more useful of "
            "the two because it can be followed, and the frontend shortens "
            "whichever shape it is given."
        ),
    )
    state: PrState = Field(
        default=PrState.RAISED,
        description=(
            "How far it got: `raised` while nobody has reviewed it, `approved` "
            "once somebody has said yes, `merged` once it is in. Omitted it is "
            "`raised`, which is where every pull request starts."
        ),
    )

    def stored(self) -> dict[str, str]:
        """This entry as the column holds it: two strings, no enum instance."""
        return {"url": self.url, "state": self.state.value}


def _clean_pr_refs(value: list[PrRef]) -> list[PrRef]:
    """Tidy a list of pull-request references into what is worth storing.

    Each URL is stripped; blanks are dropped rather than refused, because an
    empty row in the dialog's list is a box somebody has not filled in yet, not
    a mistake to reject the whole save over — the same reading `_blank_to_none`
    gives the Jira box. Duplicates are dropped too, keeping the first of each
    and the state it came with: the same pull request named twice is one pull
    request, and showing it twice on the card would only invite the reader to
    look for a difference between them — which, now that each one carries a
    state, there could genuinely be.

    Order is kept, because it is the order somebody put them in and there is no
    better one to impose — a card's first pull request is usually its main one.
    """
    kept: list[PrRef] = []
    seen: set[str] = set()
    for entry in value:
        url = entry.url.strip()
        if not url or url in seen:
            continue
        seen.add(url)
        kept.append(PrRef(url=url, state=entry.state))
    if len(kept) > MAX_PR_REFS:
        raise ValueError(f"a card may name at most {MAX_PR_REFS} pull requests")
    return kept


def _clean_sub_statuses(value: list[str]) -> list[str]:
    """Strip each label and refuse a blank one — a stage with no name is
    nothing to advance towards."""
    cleaned = [label.strip() for label in value]
    if any(not label for label in cleaned):
        raise ValueError("a sub-status label must not be blank")
    return cleaned


class ColumnDueDateInput(Command):
    """A date this card is wanted in one column by."""

    column_id: UUID = Field(
        description=(
            "One of the project's columns, and not the last one: a card is done "
            "when it reaches the end of the board, so the date for the last "
            "column is the card's own `due_date`."
        )
    )
    due_date: date


def _one_date_per_column(value: list[ColumnDueDateInput]) -> list[ColumnDueDateInput]:
    """Refuse a column named twice: two dates for one column is two answers."""
    named = [entry.column_id for entry in value]
    if len(named) != len(set(named)):
        raise ValueError("a column can only be given one due date")
    return value


class TaskCreate(Command):
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
    sub_statuses: list[SubStageLabel] = Field(
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
    assignee_id: UUID | None = Field(
        default=None,
        description=(
            "Who owns it — a member of the project. Omit it and the card goes "
            "to whoever is creating it, which is where most cards start; hand "
            "it on afterwards with `PATCH /tasks/{ref}`, or name somebody else "
            "here to begin with."
        ),
    )
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
    jira_ref: str | None = Field(
        default=None,
        max_length=REF_MAX_LENGTH,
        description="The Jira issue this card tracks — a key such as `ATL-41`, or the whole URL.",
    )
    pr_refs: list[PrRef] = Field(
        default_factory=list,
        description=(
            "The pull requests this card's work lands as — each a URL or a short "
            "form such as `#212`, and how far it got. Several are expected: a "
            "change that took a backend pull request and a frontend one is still "
            "one card. A state left off is `raised`. Blank and repeated entries "
            "are dropped, and the order is kept."
        ),
    )

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

    @field_validator("jira_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("pr_refs")
    @classmethod
    def _clean_prs(cls, value: list[PrRef]) -> list[PrRef]:
        return _clean_pr_refs(value)


class ChecklistItemCreate(Command):
    """A sub-task that is a tick box: a line of text and nothing else."""

    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ChecklistItemUpdate(Command):
    """Retitle an item, or tick, cancel or reopen it. Both fields optional."""

    title: str | None = Field(default=None, min_length=1, max_length=200)
    state: ChecklistState | None = None

    @field_validator("title")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class TaskUpdate(Command):
    """Every field optional; omitted fields are left as they are.

    Status is not here: it moves through ``POST /tasks/{ref}/status``, which is
    the only path that can demand a reason and record one.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    type: TaskType | None = None
    priority: TaskPriority | None = None
    sub_statuses: list[SubStageLabel] | None = Field(
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
    jira_ref: str | None = Field(
        default=None,
        max_length=REF_MAX_LENGTH,
        description=(
            "A different Jira issue, or null to take the reference off. Like "
            "`due_date`, null here means clear rather than leave alone."
        ),
    )
    pr_refs: list[PrRef] | None = Field(
        default=None,
        description=(
            "The whole list of pull requests this card is carried by, replacing "
            "whatever it had: this is how one is added, how one is removed, and "
            "how one is moved from `raised` to `approved` to `merged`. An empty "
            "list takes them all off, and leaving the field out leaves them "
            "alone — which is why null is not how they are cleared here."
        ),
    )

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

    @field_validator("jira_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)

    @field_validator("pr_refs")
    @classmethod
    def _clean_prs(cls, value: list[PrRef] | None) -> list[PrRef] | None:
        return None if value is None else _clean_pr_refs(value)


class TaskMove(Command):
    """Puts a card in a column at a position.

    Any column at any time, unless the card's template has stages — then,
    any column one of its stages names. Leaving a column is itself refused
    until the card is on the last sub-stage its template set for that column.

    A sub-task cannot be moved at all: it is not on the board.
    """

    column_id: UUID
    position: int = Field(
        default=0,
        ge=0,
        description="How many cards from the top of the column to put it, not a sort key. "
        "Clamped to the column's length.",
    )
    outcome: str | None = Field(
        default=None,
        description=(
            "Which of the column's outcomes the card lands on, by name — 'Done', 'Cancelled', "
            "'In prod'. Only the board's last column has any, and only if the board was "
            "divided that way. Left out, a card arriving there lands on the first one; "
            "moving anywhere else clears the card's outcome whatever this says."
        ),
    )


class TaskStatusChange(Command):
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


class CommentCreate(Command):
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
