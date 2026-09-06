"""Board columns."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from app.models.board import (
    MAX_COLUMNS,
    MAX_OUTCOMES,
    MIN_COLUMNS,
    OUTCOME_LABEL_MAX_LENGTH,
)
from app.schemas.common import Schema

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


class ColumnCreate(Schema):
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


class ColumnUpdate(Schema):
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


class ColumnOrder(Schema):
    """Reorders a board left to right."""

    column_ids: list[UUID] = Field(
        description=(
            "Every column on the board, in the order you want them. "
            "Naming a subset is an error rather than a partial reorder."
        )
    )


class ColumnRead(Schema):
    id: UUID
    project_id: UUID
    name: str
    description: str
    position: int
    outcomes: list[str] = Field(
        description=(
            "The sections this column is divided into, left to right. Empty on every column "
            "but the board's last, and on a last column that draws no distinction. A card in "
            "this column names its own by `outcome_index`."
        )
    )
    task_count: int = Field(description="How many cards are currently in this column.")


class Board(Schema):
    """A board's columns, left to right, with the limits that govern them."""

    columns: list[ColumnRead]
    min_columns: int = MIN_COLUMNS
    max_columns: int = MAX_COLUMNS
    max_outcomes: int = MAX_OUTCOMES
