"""Make a task's due date optional.

A date is now something a card may have rather than something it must invent —
see ``app.models.task.Task.due_date``. Nothing is back-filled and nothing is
cleared: every row already has a date, and every one of them was a date
somebody meant.

Revision ID: 0012
Revises: 0011
Created: 2026-09-02
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("task", "due_date", existing_type=sa.Date(), nullable=True)


def downgrade() -> None:
    # Any date this could invent would be a date nobody chose, and it would
    # show on the board as a deadline. Refuse and say what to do, as revisions
    # 0006 and 0009 do for the same reason.
    undated = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM task WHERE due_date IS NULL"))
        .scalar_one()
    )
    if undated:
        raise RuntimeError(
            f"{undated} task(s) have no due date, which revision 0011 cannot represent. "
            "Give them one before downgrading."
        )

    op.alter_column("task", "due_date", existing_type=sa.Date(), nullable=False)
