"""A card can be dated per column, not only at the end of the board.

One new table. Nothing is back-filled: a card that had one due date still has
exactly that date, and it still means the day the work is wanted finished —
which is the day it is wanted in the last column. Everything here is the dates
*before* that one.

``column_id`` is ``ON DELETE CASCADE`` where ``task.column_id`` has no
``ondelete`` at all, and the difference is deliberate: a column holding cards
must not be deleted out from under them, while a deadline for a column that no
longer exists is a date with nothing to be due in.

Revision ID: 0017
Revises: 0016
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_column_due_date",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name=op.f("fk_task_column_due_date_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["board_column.id"],
            name=op.f("fk_task_column_due_date_column_id_board_column"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "column_id", name=op.f("pk_task_column_due_date")),
    )


def downgrade() -> None:
    op.drop_table("task_column_due_date")
