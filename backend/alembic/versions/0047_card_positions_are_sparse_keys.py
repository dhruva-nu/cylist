"""Card positions are sparse sort keys, unique within a column.

A card's ``position`` was its index in the column, contiguous from zero, so
every drag rewrote every card below the one dropped — and with no lock and no
unique key, two drags at once could leave two cards at one position with no
order between them (CYLIST-127). Positions become keys with room between
them, and a move writes only the card that moved; see
``app.services.tasks.stacking``.

Every column is spread out here: each card keeps its place, ties included
(broken by id, the order a reader most likely saw), and is given a key a gap
apart. The spacing shrinks for a column so tall that the gap would overflow a
32-bit integer. Only then can the plain index become a unique constraint,
deferrable so a statement that spreads a column out is checked as a whole.

The downgrade numbers each column from zero again, which is what the code
before this revision expects to find.

Revision ID: 0047
Revises: 0046
Created: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        UPDATE task
        SET position = ranked.rank * LEAST(65536, 2147483647 / (ranked.total + 1))
        FROM (
            SELECT
                id,
                row_number() OVER (PARTITION BY column_id ORDER BY position, id) AS rank,
                count(*) OVER (PARTITION BY column_id) AS total
            FROM task
            WHERE column_id IS NOT NULL
        ) AS ranked
        WHERE task.id = ranked.id
        """
    )
    op.drop_index("ix_task_column_id_position", table_name="task")
    op.create_unique_constraint(
        "uq_task_column_id_position",
        "task",
        ["column_id", "position"],
        deferrable=True,
        initially="IMMEDIATE",
    )


def downgrade() -> None:
    op.drop_constraint("uq_task_column_id_position", "task", type_="unique")
    op.create_index("ix_task_column_id_position", "task", ["column_id", "position"])
    op.execute(
        """
        UPDATE task
        SET position = ranked.rank - 1
        FROM (
            SELECT id, row_number() OVER (PARTITION BY column_id ORDER BY position, id) AS rank
            FROM task
            WHERE column_id IS NOT NULL
        ) AS ranked
        WHERE task.id = ranked.id
        """
    )
