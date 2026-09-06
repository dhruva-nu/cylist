"""A card in the board's last column carries a finishing time of its own.

``finished_at`` belonged to sub-tasks alone, and a check constraint said so:
done was a *place* for a card — the board's last column — and a *moment* for a
sub-task, and one row holding both answers could have them disagree.

The disagreement is real but the constraint fixed it the wrong way round. A
place answers "is it done now"; it cannot answer "when", and it cannot answer
at all for a card that was finished in June and dragged back out in July —
which the day's report, a goal's progress and the card's own history all have
to be able to say. So the field is written for cards too, on the way into the
last column and cleared on the way out, and the constraint goes: no row can see
the board it is on, and which column is last changes the moment one is added to
its right.

Back-filled the way 0014 back-filled its sub-tasks, and for the same reason:
every card presently sitting in its board's last column is recorded as finished
at the moment it was last touched, which is the closest thing to a finishing
time the old model kept. Nothing else changes — no card moves, and a board that
had no last column to speak of behaves exactly as it did.

Revision ID: 0016
Revises: 0015
Created: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("finished_only_by_subtasks", "task", type_="check")

    op.execute(
        sa.text(
            """
            UPDATE task AS card
            SET finished_at = card.updated_at
            WHERE card.parent_id IS NULL
              AND card.finished_at IS NULL
              AND card.column_id = (
                  SELECT board_column.id
                  FROM board_column
                  WHERE board_column.project_id = card.project_id
                  ORDER BY board_column.position DESC
                  LIMIT 1
              )
            """
        )
    )


def downgrade() -> None:
    # The constraint cannot come back over cards that hold a time, and the time
    # is exactly what the old model had nowhere to keep. Dropping it loses only
    # what could not have been recorded before this migration ran.
    op.execute(sa.text("UPDATE task SET finished_at = NULL WHERE parent_id IS NULL"))
    op.create_check_constraint(
        "finished_only_by_subtasks",
        "task",
        "finished_at IS NULL OR parent_id IS NOT NULL",
    )
