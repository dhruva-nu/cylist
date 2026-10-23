"""Goals: the epic a card belongs to, and how far along it is."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field

from app.models.goal import GoalStatus
from app.schemas.common import Page, Schema
from app.schemas.people import PersonRead
from app.schemas.tasks import TaskSummary
from app.services.commands.goals import (
    _COLOUR_PATTERN as _COLOUR_PATTERN,
)
from app.services.commands.goals import (
    GoalCreate as GoalCreate,
)
from app.services.commands.goals import (
    GoalUpdate as GoalUpdate,
)
from app.services.commands.goals import (
    _stripped as _stripped,
)
from app.services.readmodels.goals import (
    GoalProgress as GoalProgress,
)


class GoalRead(Schema):
    id: UUID
    project_id: UUID
    reference: str = Field(description="`ATL-G1`. Usable in place of the id.")
    number: int = Field(description="Per-project goal number. The `1` in `ATL-G1`.")
    name: str
    description: str
    colour: str = Field(description="Six-digit hex, drawn as the rail on this goal's cards.")
    status: GoalStatus
    target_date: date | None = Field(description="Null when the goal has no date.")
    achieved_at: datetime | None = Field(description="When it was reached. Null until it is.")
    owner: PersonRead
    progress: GoalProgress
    created_at: datetime


class GoalDetail(GoalRead):
    """One goal and the first page of the cards linked to it.

    The cards come back in board order — left to right by column, top to
    bottom within one — so a goal's page can be read as the slice of the board
    that belongs to it. Its cards in cold storage follow, most recently
    finished first.
    """

    tasks: list[TaskSummary] = Field(
        description="The first page of the goal's cards. `progress.total` says how many in all."
    )
    tasks_next: UUID | None = Field(
        description="Pass as `after` to `GET /goals/{ref}/tasks` for the cards after these. "
        "Null when `tasks` is all of them."
    )


class GoalPage(Page[GoalRead]):
    """A page of a project's goals."""
