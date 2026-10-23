"""The commands the columns services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.columns``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.models.board import MAX_OUTCOMES, OUTCOME_LABEL_MAX_LENGTH
from app.services.commands.base import Command

_OUTCOMES: list[str] = Field(
    default_factory=list,
    max_length=MAX_OUTCOMES,
    description=(
        "The ways work can end in this column — 'Done', 'Cancelled', 'In prod' — as sections "
        "the column is divided into, left to right. Only the board's last column may have any; "
        "anywhere else this must be empty. Leave it empty to draw no distinction."
    ),
)


def _clean_outcomes(value: list[str]) -> list[str]:
    """Trim the labels and refuse a blank or a repeat.

    Two sections called the same thing are two places a card could equally be
    said to have landed, which makes the section it is in unreadable on the
    board and the answer to "how did this end" ambiguous everywhere else.
    """
    cleaned = [label.strip() for label in value]
    if any(not label for label in cleaned):
        raise ValueError("an outcome must not be blank")
    if any(len(label) > OUTCOME_LABEL_MAX_LENGTH for label in cleaned):
        raise ValueError(f"an outcome is at most {OUTCOME_LABEL_MAX_LENGTH} characters")
    if len({label.casefold() for label in cleaned}) != len(cleaned):
        raise ValueError("name each outcome once")
    return cleaned


class ColumnCreate(Command):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(
        min_length=1,
        max_length=500,
        description="What belongs in this column. Required — an unexplained column drifts.",
    )
    outcomes: list[str] = _OUTCOMES

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped

    @field_validator("outcomes")
    @classmethod
    def _outcomes(cls, value: list[str]) -> list[str]:
        return _clean_outcomes(value)


class ColumnUpdate(Command):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=500)
    outcomes: list[str] | None = Field(
        default=None,
        max_length=MAX_OUTCOMES,
        description=(
            "Replaces the column's sections outright. Shortening the list moves any card past "
            "its end into the last section still standing; emptying it takes every card in the "
            "column out of a section altogether."
        ),
    )

    @field_validator("outcomes")
    @classmethod
    def _outcomes(cls, value: list[str] | None) -> list[str] | None:
        return None if value is None else _clean_outcomes(value)
