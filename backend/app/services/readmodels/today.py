"""What the today services return.

Plain values the services build and hand back. The HTTP layer re-exports them
(``app.schemas.today``) as the responses its routes declare, so there is one definition
and the services import nothing from the HTTP layer.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.models.today import TodoState
from app.services.readmodels.base import ReadModel


class TodoRead(ReadModel):
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


class PickedCard(ReadModel):
    """The card a pick is of: enough to name it without a second request."""

    id: UUID
    reference: str
    title: str
    column: str | None = Field(
        description="The column it sits in now. Null once it has gone to cold storage."
    )


class PickRead(ReadModel):
    """One card on the caller's plan for a day, with their to-dos under it."""

    id: UUID
    day: date
    task: PickedCard
    todos: list[TodoRead]
