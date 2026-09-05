"""Goals: the epic a card belongs to, and how far along it is."""

from __future__ import annotations

from datetime import date, datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.goal import NAME_MAX_LENGTH, GoalStatus
from app.schemas.common import Schema
from app.schemas.people import PersonRead
from app.schemas.tasks import TaskRead

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


def _stripped(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class GoalCreate(Schema):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(
        default="",
        max_length=5000,
        description=(
            "What reaching this goal means. Optional, unlike a card's — a goal "
            "is a heading its cards spell out underneath."
        ),
    )
    colour: str | None = Field(
        default=None,
        pattern=_COLOUR_PATTERN,
        description=(
            "Six-digit hex, drawn as the rail down every card linked to this "
            "goal. Omit to take a stable colour from the palette."
        ),
    )
    target_date: date | None = Field(
        default=None,
        description="When the goal is wanted by. Omit it for a goal with no date.",
    )
    owner_id: UUID = Field(description="Must be a member of the project.")

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _stripped(value)

    @field_validator("description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class GoalUpdate(Schema):
    """Every field optional; omitted fields are left as they are.

    ``status`` is here rather than on a path of its own, unlike a task's: a
    task's status change has to carry a reason and be written to a timeline,
    while a goal's is one word with nothing owed alongside it. The rule it
    does carry — that a goal cannot be achieved over open cards — is enforced
    wherever it is set.
    """

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=5000)
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)
    target_date: date | None = Field(
        default=None,
        description=(
            "A new date, or null to take the date off the goal. Unlike the "
            "other fields here, null means clear rather than leave alone."
        ),
    )
    owner_id: UUID | None = None
    status: GoalStatus | None = Field(
        default=None,
        description=(
            "`achieved` is refused while a linked card is still open — the "
            "error names what is outstanding. `dropped` is not: giving up on a "
            "goal is exactly the thing you do while work on it is unfinished."
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str | None) -> str | None:
        return None if value is None else _stripped(value)

    @field_validator("description")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()


class GoalProgress(Schema):
    """How far along a goal is, counted from the cards linked to it.

    Derived rather than stored: a goal's progress is a reading of its cards,
    and a percentage kept in a column beside them is a number that can be
    wrong. Cards only — a sub-task belongs to its card, and its card is what
    the goal counts.
    """

    total: int = Field(description="Cards linked to this goal, cancelled ones included.")
    done: int = Field(
        description="Cards in the board's last column. What 'done' means for a card is where "
        "it is, so it is where this is counted from."
    )
    cancelled: int = Field(description="Cards dropped. Settled, but not achieved.")
    open: int = Field(
        description="Cards that are neither done nor cancelled — what is left. While this is "
        "above zero the goal cannot be marked achieved."
    )
    blocked: int = Field(description="Open cards that cannot proceed.")
    on_hold: int = Field(description="Open cards deliberately paused.")


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
    """One goal and the cards linked to it.

    The cards come back in board order — left to right by column, top to
    bottom within one — so a goal's page can be read as the slice of the board
    that belongs to it.
    """

    tasks: list[TaskRead]
