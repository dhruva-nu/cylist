"""Sub-tasks come off the board: no column, and a finishing time of their own.

A sub-task was a card in a column sitting beside the card it belonged to, which
meant one piece of work was dealt out across the board as several peers and
every count the board reported — per column, per person, per day — was adding
up two different kinds of thing. So ``column_id`` and ``position`` become the
property of top-level cards alone.

That takes away the only definition of a finished sub-task there was. "Done" was
literally *in the board's last column*, so with no column there is nothing left
to compare against: ``finished_at`` is where done goes instead, and it is barred
from top-level cards, which the board still answers for.

Back-filled rather than reset. Every sub-task presently sitting in its board's
last column is recorded as finished at the moment it was last touched, which is
the closest thing to a finishing time the old model ever kept; every other
sub-task is left open. Then, and only then, the two placement columns are
cleared for anything with a parent. Nothing is deleted and no reference changes:
``ATL-41-2`` still names the same row, with the same comments and the same
history.

Revision ID: 0014
Revises: 0013
Created: 2026-09-05
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task", sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True))

    # A sub-task in the last column of its own project's board was finished, and
    # `updated_at` is the last moment anything happened to it — the nearest a
    # row can get to when that was. Written before the column is cleared,
    # because the column is the only evidence.
    op.execute(
        sa.text(
            """
            UPDATE task AS child
            SET finished_at = child.updated_at
            WHERE child.parent_id IS NOT NULL
              AND child.column_id = (
                  SELECT board_column.id
                  FROM board_column
                  WHERE board_column.project_id = child.project_id
                  ORDER BY board_column.position DESC
                  LIMIT 1
              )
            """
        )
    )

    op.alter_column("task", "column_id", existing_type=sa.dialects.postgresql.UUID(), nullable=True)
    op.alter_column("task", "position", existing_type=sa.Integer(), nullable=True)

    op.execute(
        sa.text("UPDATE task SET column_id = NULL, position = NULL WHERE parent_id IS NOT NULL")
    )

    # The gaps the departed sub-tasks left in their columns, closed. Positions
    # are contiguous from zero everywhere else in the system and code that
    # inserts a card counts on it.
    op.execute(
        sa.text(
            """
            UPDATE task
            SET position = renumbered.rank - 1
            FROM (
                SELECT id, ROW_NUMBER() OVER (PARTITION BY column_id ORDER BY position, id) AS rank
                FROM task
                WHERE column_id IS NOT NULL
            ) AS renumbered
            WHERE task.id = renumbered.id AND task.position <> renumbered.rank - 1
            """
        )
    )

    op.create_check_constraint(
        "placed_by_parentage",
        "task",
        "(parent_id IS NULL) = (column_id IS NOT NULL) "
        "AND (parent_id IS NULL) = (position IS NOT NULL)",
    )
    op.create_check_constraint(
        "finished_only_by_subtasks",
        "task",
        "finished_at IS NULL OR parent_id IS NOT NULL",
    )


def downgrade() -> None:
    op.drop_constraint("finished_only_by_subtasks", "task", type_="check")
    op.drop_constraint("placed_by_parentage", "task", type_="check")

    # Put every sub-task back on the board, in the column the old model would
    # have read its state from: the last one if it was finished, the first one
    # if it was not. Where it sat before it came off the board is recorded
    # nowhere, so this is the honest reconstruction rather than the exact one.
    op.execute(
        sa.text(
            """
            UPDATE task AS child
            SET column_id = (
                SELECT board_column.id
                FROM board_column
                WHERE board_column.project_id = child.project_id
                ORDER BY board_column.position DESC
                LIMIT 1
            )
            WHERE child.parent_id IS NOT NULL AND child.finished_at IS NOT NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE task AS child
            SET column_id = (
                SELECT board_column.id
                FROM board_column
                WHERE board_column.project_id = child.project_id
                ORDER BY board_column.position ASC
                LIMIT 1
            )
            WHERE child.parent_id IS NOT NULL AND child.finished_at IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE task
            SET position = renumbered.rank - 1
            FROM (
                SELECT id,
                       ROW_NUMBER() OVER (
                           PARTITION BY column_id ORDER BY position NULLS LAST, id
                       ) AS rank
                FROM task
                WHERE column_id IS NOT NULL
            ) AS renumbered
            WHERE task.id = renumbered.id
            """
        )
    )

    op.alter_column(
        "task", "column_id", existing_type=sa.dialects.postgresql.UUID(), nullable=False
    )
    op.alter_column("task", "position", existing_type=sa.Integer(), nullable=False)
    op.drop_column("task", "finished_at")
