"""A person's plan for a day: wire shapes."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.today import TEXT_MAX_LENGTH, TodoState
from app.schemas.common import Schema


class TodoRead(Schema):
    """One thing to do about a card, on one day."""

    id: UUID
    text: str = Field(
        description="What to do, as written — `@Aditi K` and `>brief.pdf` tags included."
    )
    state: TodoState = Field(
        description="`open`, `done`, `moved` (handed on at wrap-up — `moved_to` says "
        "where) or `not_done` (left behind at wrap-up)."
    )
    done_at: datetime | None
    moved_to: date | None = Field(description="The day it was handed on to, when it was.")
    carried_from: date | None = Field(
        description="The day it was handed on from, when it arrived by wrap-up."
    )
    source_kind: Literal["stage", "subtask", "checklist"] | None = Field(
        description="What on the card it stands for, when it came from the card: a stage, "
        "a sub-task or a tick box. Ticking it ticks that too, and the other way about. "
        "Null for a to-do somebody wrote."
    )
    created_at: datetime


class PickedCard(Schema):
    """The card a pick is of: enough to name it without a second request."""

    id: UUID
    reference: str
    title: str
    column: str | None = Field(
        description="The column it sits in now. Null once it has gone to cold storage."
    )


class PickRead(Schema):
    """One card on the caller's plan for a day, with their to-dos under it."""

    id: UUID
    day: date
    task: PickedCard
    todos: list[TodoRead]


class TodayPlan(Schema):
    """The caller's plan for one day on one project."""

    project_key: str
    day: date
    timezone: str
    picks: list[PickRead] = Field(description="In the order the cards were picked.")
    open_count: int = Field(description="To-dos still open on the day.")
    done_count: int


class PickCreate(BaseModel):
    task: str = Field(description="The card, by reference such as `ATL-41` or by id.")
    day: date = Field(description="Which day to put it on, as `YYYY-MM-DD`.")


class TodoCreate(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)


class TodoUpdate(BaseModel):
    text: str | None = Field(default=None, min_length=1, max_length=TEXT_MAX_LENGTH)
    state: Literal["open", "done", "not_done"] | None = Field(
        default=None,
        description="Tick it (`done`), untick it (`open`) or give up on it (`not_done`). "
        "`moved` is what wrap-up sets, never this.",
    )


class WrapUp(BaseModel):
    day: date = Field(description="The day being wrapped up.")
    to_day: date = Field(description="Where the moved to-dos go. Must be after `day`.")
    move: list[UUID] = Field(
        default_factory=list,
        description="The open to-dos to hand on. Every other open to-do on the day "
        "stays behind as `not_done`.",
    )


class WrapUpResult(Schema):
    day: date
    to_day: date
    moved: int
    not_done: int
