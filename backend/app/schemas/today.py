"""A person's plan for a day: wire shapes."""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import BaseModel, Field

from app.models.today import TEXT_MAX_LENGTH
from app.schemas.common import Schema
from app.services.commands.today import (
    TodoUpdate as TodoUpdate,
)
from app.services.readmodels.today import (
    PickedCard as PickedCard,
)
from app.services.readmodels.today import (
    PickRead as PickRead,
)
from app.services.readmodels.today import (
    TodoRead as TodoRead,
)


class TodayPlan(Schema):
    """The caller's plan for one day on one project."""

    project_key: str
    day: date
    timezone: str
    picks: list[PickRead] = Field(description="In the order the cards were picked.")
    free_todos: list[TodoRead] = Field(
        description="To-dos about no card in particular, in the order written."
    )
    open_count: int = Field(
        description="To-dos still open on the day, those about no card included."
    )
    done_count: int


class PickCreate(BaseModel):
    task: str = Field(description="The card, by reference such as `ATL-41` or by id.")
    day: date = Field(description="Which day to put it on, as `YYYY-MM-DD`.")


class TodoCreate(BaseModel):
    text: str = Field(min_length=1, max_length=TEXT_MAX_LENGTH)


class FreeTodoCreate(TodoCreate):
    day: date = Field(description="Which day it is for, as `YYYY-MM-DD`.")


class WrapUp(BaseModel):
    day: date = Field(description="The day being wrapped up.")
    to_day: date = Field(description="Where the moved to-dos go. Must be after `day`.")
    move: list[UUID] = Field(
        default_factory=list,
        description="The open to-dos to hand on. Every other open to-do on the day "
        "stays behind as `not_done`.",
    )
    timezone: str | None = Field(
        default=None,
        description="IANA zone that says what today is, e.g. `Asia/Kolkata`. Defaults to UTC. "
        "A day from today on is brought in step with its cards before it is closed; "
        "a day gone by is closed on the plan it had.",
    )


class WrapUpResult(Schema):
    day: date
    to_day: date
    moved: int
    not_done: int
