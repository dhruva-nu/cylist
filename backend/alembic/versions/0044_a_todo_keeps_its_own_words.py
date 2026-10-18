"""A to-do keeps its own words, and a stage to-do names its column.

Two fixes to how a day's plan follows its cards (CYLIST-96):

* ``todo.reworded`` says somebody rewrote an imported to-do's text. Keeping
  the plan in step with its card used to put the card's wording back on every
  read; a reworded one now keeps what it was given.
* A stage to-do's ``source`` was ``stage:<index>``. A card's stages are
  replaced when it changes column, so index 0 in one column was taken for
  index 0 in the next — a stage ticked today turned back into an open to-do
  named for a different stage. It is now ``stage:<column id>:<index>``.
  Existing ones are given the column their card is in now, which is the one
  they were imported from unless the card has moved since; a stored card's
  are left as they were and simply stop following it.

The downgrade drops the column and unlinks the stage to-dos it rewrote
(``source`` to null), since two columns' ``stage:0`` on one pick would collide
going back to the old form.

Revision ID: 0044
Revises: 0043
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "todo",
        sa.Column("reworded", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute(
        """
        UPDATE todo
        SET source = 'stage:' || task.column_id::text || ':' || substring(todo.source from 7)
        FROM day_pick
        JOIN task ON task.id = day_pick.task_id
        WHERE todo.pick_id = day_pick.id
          AND todo.source ~ '^stage:[0-9]+$'
          AND task.column_id IS NOT NULL
        """
    )


def downgrade() -> None:
    op.execute("UPDATE todo SET source = NULL WHERE source ~ '^stage:[0-9a-f-]{36}:[0-9]+$'")
    op.drop_column("todo", "reworded")
