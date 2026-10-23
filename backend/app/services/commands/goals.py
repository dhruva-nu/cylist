"""The commands the goals services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.goals``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from pydantic import Field, field_validator

from app.models.goal import NAME_MAX_LENGTH, GoalStatus
from app.services.commands.base import Command

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


def _stripped(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class GoalCreate(Command):
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


class GoalUpdate(Command):
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
