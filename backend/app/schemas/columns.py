"""Board columns."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.models.board import (
    MAX_COLUMNS,
    MAX_OUTCOMES,
    MIN_COLUMNS,
)
from app.schemas.common import Schema
from app.services.commands.columns import (
    _OUTCOMES as _OUTCOMES,
)
from app.services.commands.columns import (
    ColumnCreate as ColumnCreate,
)
from app.services.commands.columns import (
    ColumnUpdate as ColumnUpdate,
)
from app.services.commands.columns import (
    _clean_outcomes as _clean_outcomes,
)


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
