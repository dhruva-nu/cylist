"""Task priority: 0 (urgent) to 3 (someday), color-flagged on the board.

A non-native enum, like every other one on this table (see ``task_status``,
``task_type``) — gaining a member later is a code change, not a migration.

Every existing row backfills to ``someday``, the least urgent end of the
scale: nothing gets retroactively flagged urgent just for predating this
column.

Revision ID: 0010
Revises: 0009
Created: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

_PRIORITIES = ("urgent", "asap", "week", "someday")

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column(
            "priority",
            sa.Enum(*_PRIORITIES, name="task_priority", native_enum=False),
            server_default=sa.text("'someday'"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("task", "priority")
