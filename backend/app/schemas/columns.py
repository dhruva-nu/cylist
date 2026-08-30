"""Board columns."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from app.models.board import MAX_COLUMNS, MIN_COLUMNS
from app.schemas.common import Schema


class ColumnCreate(Schema):
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(
        min_length=1,
        max_length=500,
        description="What belongs in this column. Required — an unexplained column drifts.",
    )

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class ColumnUpdate(Schema):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=80)
    description: str | None = Field(default=None, min_length=1, max_length=500)


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
    task_count: int = Field(description="How many cards are currently in this column.")


class Board(Schema):
    """A board's columns, left to right, with the limits that govern them."""

    columns: list[ColumnRead]
    min_columns: int = MIN_COLUMNS
    max_columns: int = MAX_COLUMNS
