"""Board columns.

A board is its columns, left to right. ``position`` is not decoration: the
leftmost column is where every new task lands, so the order of this table is
part of the workflow rather than a display preference.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

MIN_COLUMNS = 2
"""Below two columns nothing can move, which is not a board but a list."""

MAX_COLUMNS = 8
"""Past eight the board scrolls sideways further than it can be read at a
glance, and a board you have to scan is a board you stop looking at."""


class BoardColumn(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "board_column"
    __table_args__ = (Index("ix_board_column_project_id_position", "project_id", "position"),)

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
