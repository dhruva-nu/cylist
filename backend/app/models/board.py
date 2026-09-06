"""Board columns.

A board is its columns, left to right. ``position`` is not decoration: the
leftmost column is where every new task lands, so the order of this table is
part of the workflow rather than a display preference.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, Index, Integer, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MIN_COLUMNS = 2
"""Below two columns nothing can move, which is not a board but a list."""

MAX_COLUMNS = 8
"""Past eight the board scrolls sideways further than it can be read at a
glance, and a board you have to scan is a board you stop looking at."""

OUTCOME_LABEL_MAX_LENGTH = 40

MAX_OUTCOMES = 3
"""Three ways work can end, and no more.

Enough for the distinctions teams actually draw — shipped, dropped, and the
one in between that this particular team cares about — and few enough that the
last column stays one column rather than becoming a second board."""


class BoardColumn(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "board_column"
    __table_args__ = (
        Index("ix_board_column_project_id_position", "project_id", "position"),
        CheckConstraint(
            f"cardinality(outcomes) <= {MAX_OUTCOMES}",
            name="outcome_max_count",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(80), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False)
    """Required, not optional flavour text: it is the only place that records
    what belongs in the column, which is what stops "Review" quietly becoming
    a second backlog."""

    position: Mapped[int] = mapped_column(Integer, nullable=False)
    """Left to right, contiguous from zero. Position 0 receives new tasks."""

    outcomes: Mapped[list[str]] = mapped_column(
        postgresql.ARRAY(String(OUTCOME_LABEL_MAX_LENGTH)),
        nullable=False,
        default=list,
        server_default=sql_text("'{}'"),
    )
    """The ways work can end here, left to right — "Done", "Cancelled", "In
    prod" — or empty, which is a column that draws no distinction.

    Only the board's last column may hold any, because an outcome is how a
    piece of work *ended* and nowhere else on the board has ended anything.
    That is a rule about a position, so it lives in
    :func:`app.services.columns.update` rather than here: no row knows whether
    it is the last one, and a column added to its right takes the title away
    without touching either row.

    Sections of one column rather than columns of their own. Three ends to a
    workflow is three more columns on a board that keeps eight, and — worse —
    it would make "done" a place the board has three of, when every count in
    the system asks the question once. See ``Task.outcome_index`` for how a
    card says which of them it landed on."""
