"""Cold storage is read a page at a time, by when its cards finished.

CYLIST-130 pages every list, and cold storage is ordered by ``finished_at``
rather than ``archived_at``: a run stores a whole column at one instant, so
every card in it shares an ``archived_at`` and that order says nothing.

A page continues from a (``finished_at``, ``id``) pair, which only works if
every stored card has a ``finished_at`` — a NULL compares as neither before
nor after anything, and the row would be on no page. A card in the last
column is finished, but one stored before Cylist kept the moment may not say
when; it is given the moment it was stored, the closest thing on record. A
check constraint then keeps it so.

The index cold storage was read by is replaced with one in the new order.

Revision ID: 0050
Revises: 0049
Created: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0050"
down_revision: str | None = "0049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "UPDATE task SET finished_at = archived_at "
        "WHERE archived_at IS NOT NULL AND finished_at IS NULL"
    )
    op.create_check_constraint(
        "archived_only_when_finished",
        "task",
        "archived_at IS NULL OR finished_at IS NOT NULL",
    )
    op.drop_index("ix_task_project_id_archived_at", table_name="task")
    op.create_index(
        "ix_task_project_id_finished_at_id",
        "task",
        ["project_id", "finished_at", "id"],
        postgresql_where=sa.text("archived_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_project_id_finished_at_id", table_name="task")
    op.create_index(
        "ix_task_project_id_archived_at",
        "task",
        ["project_id", "archived_at"],
        postgresql_where=sa.text("archived_at IS NOT NULL"),
    )
    # The finished_at given to legacy stored cards stays: it is a true enough
    # reading of when they finished, and no earlier revision depends on it
    # being NULL.
    op.drop_constraint("archived_only_when_finished", "task", type_="check")
