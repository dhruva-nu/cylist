"""A change says it has hooks to match, and the courier does the matching.

Hook matching and payload building used to run inside the transaction that made
the change, so every write paid for them — and held its locks while it did, and
failed if they did. The write now only marks its activity row ``hooks_pending``,
a column write that costs nothing; the courier takes pending rows in a
transaction of its own (``FOR UPDATE SKIP LOCKED``, so several processes split
the work), matches them against the project's hooks, queues the deliveries and
clears the flag in the same commit.

Added with a server default of false, so every row already there is not pending
and nothing is rewritten. The partial index holds only the rows waiting, which
are few and recent.

Revision ID: 0051
Revises: 0050
Created: 2026-10-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0051"
down_revision: str | None = "0050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "activity",
        sa.Column("hooks_pending", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_activity_hooks_pending",
        "activity",
        ["occurred_at", "id"],
        postgresql_where=sa.text("hooks_pending"),
    )


def downgrade() -> None:
    op.drop_index("ix_activity_hooks_pending", table_name="activity")
    op.drop_column("activity", "hooks_pending")
