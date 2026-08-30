"""Tasks, their status changes and their timeline."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.task import CommentKind, TaskStatus, TaskType
from app.schemas.common import Schema
from app.schemas.people import PersonRead


def _blank_to_none(value: str | None) -> str | None:
    """Treat an empty box in a form as "not set" rather than as an empty ref."""
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


class TaskCreate(Schema):
    """A new card. It always lands in the board's first column."""

    title: str = Field(min_length=1, max_length=200)
    description: str = Field(
        min_length=1, max_length=5000, description="What done looks like. Required."
    )
    type: TaskType
    due_date: date
    assignee_id: UUID = Field(description="Must be a member of the project.")
    jira_ref: str | None = Field(default=None, max_length=64)
    pr_ref: str | None = Field(default=None, max_length=200)

    @field_validator("title", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("jira_ref", "pr_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class TaskUpdate(Schema):
    """Every field optional; omitted fields are left as they are.

    Status is not here: it moves through ``POST /tasks/{ref}/status``, which is
    the only path that can demand a reason and record one.
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, min_length=1, max_length=5000)
    type: TaskType | None = None
    due_date: date | None = None
    assignee_id: UUID | None = None
    jira_ref: str | None = Field(default=None, max_length=64)
    pr_ref: str | None = Field(default=None, max_length=200)

    @field_validator("jira_ref", "pr_ref")
    @classmethod
    def _optional(cls, value: str | None) -> str | None:
        return _blank_to_none(value)


class TaskMove(Schema):
    """Puts a card in a column at a position. Any column, any time."""

    column_id: UUID
    position: int = Field(default=0, ge=0, description="Clamped to the column's length.")


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
    reference: str = Field(description="`ATL-41`. Usable in place of the id.")
    number: int
    column_id: UUID
    position: int
    title: str
    description: str
    type: TaskType
    due_date: date
    assignee: PersonRead
    status: TaskStatus
    jira_ref: str | None
    pr_ref: str | None
    waiting_on: list[PersonRead] = Field(
        description="Who this is waiting on. Empty unless the task is on hold or blocked."
    )
    comment_count: int
    created_at: datetime


class TaskDetail(TaskRead):
    """One task with its timeline — comments and status changes, oldest first."""

    comments: list[CommentRead]
