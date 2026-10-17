"""Finished cards can go to cold storage.

``task.archived_at`` (CYLIST-70): when a card was taken off the board into cold
storage, or null while it is on it. A stored card is in no column, so
``placed_by_parentage`` is rewritten to allow exactly that one more way for a
top-level card to have no ``column_id`` and no ``position``, and
``archived_only_on_cards`` keeps sub-tasks out of it.

Nullable with no default, so every existing card is on the board, as it was.

Revision ID: 0040
Revises: 0039
Created: 2026-10-17
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task", sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_task_project_id_archived_at",
        "task",
        ["project_id", "archived_at"],
        postgresql_where=sa.text("archived_at IS NOT NULL"),
    )

    op.drop_constraint("placed_by_parentage", "task", type_="check")
    op.create_check_constraint(
        "placed_by_parentage",
        "task",
        "(parent_id IS NULL AND archived_at IS NULL) = (column_id IS NOT NULL) "
        "AND (parent_id IS NULL AND archived_at IS NULL) = (position IS NOT NULL)",
    )
    op.create_check_constraint(
        "archived_only_on_cards",
        "task",
        "archived_at IS NULL OR parent_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("archived_only_on_cards", "task", type_="check")

    # Every stored card goes back on the board before the column that says it
    # was stored goes away: to the bottom of the board's last column, in the
    # order they were stored, which is where restoring each by hand would have
    # put them.
    op.execute(
        sa.text(
            """
            WITH last_column AS (
                SELECT DISTINCT ON (project_id) project_id, id
                FROM board_column
                ORDER BY project_id, position DESC
            ),
            depth AS (
                SELECT column_id, count(*) AS cards
                FROM task
                WHERE column_id IS NOT NULL
                GROUP BY column_id
            ),
            stored AS (
                SELECT
                    task.id,
                    last_column.id AS column_id,
                    coalesce(depth.cards, 0)
                        + row_number() OVER (
                            PARTITION BY task.project_id ORDER BY task.archived_at, task.number
                        )
                        - 1 AS position
                FROM task
                JOIN last_column ON last_column.project_id = task.project_id
                LEFT JOIN depth ON depth.column_id = last_column.id
                WHERE task.archived_at IS NOT NULL
            )
            UPDATE task
            SET column_id = stored.column_id, position = stored.position, archived_at = NULL
            FROM stored
            WHERE task.id = stored.id
            """
        )
    )

    op.drop_constraint("placed_by_parentage", "task", type_="check")
    op.create_check_constraint(
        "placed_by_parentage",
        "task",
        "(parent_id IS NULL) = (column_id IS NOT NULL) "
        "AND (parent_id IS NULL) = (position IS NOT NULL)",
    )
    op.drop_index("ix_task_project_id_archived_at", table_name="task")
    op.drop_column("task", "archived_at")
