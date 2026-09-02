"""Task sub-status: up to 4 stage labels and a pointer into them.

A progress bar within a card rather than a column of its own — see
``app.models.task.Task.sub_statuses``. Every existing row backfills to an
empty list and a null pointer, which is exactly the "not using this" state:
nothing is retroactively put on a stage it was never given.

Revision ID: 0011
Revises: 0010
Created: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column(
            "sub_statuses",
            postgresql.ARRAY(sa.String(60)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.add_column("task", sa.Column("sub_status_index", sa.Integer(), nullable=True))
    op.create_check_constraint("sub_status_max_four", "task", "cardinality(sub_statuses) <= 4")
    op.create_check_constraint(
        "sub_status_index_matches_list",
        "task",
        "(sub_status_index IS NULL) = (cardinality(sub_statuses) = 0)",
    )
    op.create_check_constraint(
        "sub_status_index_in_range",
        "task",
        "sub_status_index IS NULL "
        "OR (sub_status_index >= 0 AND sub_status_index < cardinality(sub_statuses))",
    )


def downgrade() -> None:
    op.drop_constraint("sub_status_index_in_range", "task", type_="check")
    op.drop_constraint("sub_status_index_matches_list", "task", type_="check")
    op.drop_constraint("sub_status_max_four", "task", type_="check")
    op.drop_column("task", "sub_status_index")
    op.drop_column("task", "sub_statuses")
