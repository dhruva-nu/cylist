"""The last column is divided into outcomes, and a card says which it ended on.

Three new fields and no new tables. ``board_column.outcomes`` names the up-to-
three sections the board's last column is divided into — "Done", "Cancelled",
"In prod" — and ``task.outcome_index`` says which of them a card is in.
``template_stage.allowed_outcomes`` is the template's say in it: which of those
sections its own cards are allowed to end on.

Every one of them starts empty, which is the state every existing board is
already in: a column with no outcomes draws no distinction, a card with no
index is in no section, and a stage that names no outcomes allows all of them.
So nothing is back-filled and no board changes until somebody divides one.

Two rules are absent from here on purpose. That only the *last* column may hold
outcomes is a fact about a position no row can see, and that an index has to
point at a section that exists is a fact about another table's array; both live
in ``app.services.columns``. What the constraints below do say is the part a row
can answer for itself: at most three of them, and only a card — never a
sub-task, which is not on the board and so is in no column to have sections.

Revision ID: 0017
Revises: 0016
Created: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MAX_OUTCOMES = 3
_LABEL = 40


def upgrade() -> None:
    op.add_column(
        "board_column",
        sa.Column(
            "outcomes",
            postgresql.ARRAY(sa.String(length=_LABEL)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "outcome_max_count",
        "board_column",
        f"cardinality(outcomes) <= {_MAX_OUTCOMES}",
    )

    op.add_column(
        "template_stage",
        sa.Column(
            "allowed_outcomes",
            postgresql.ARRAY(sa.String(length=_LABEL)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "allowed_outcome_max_count",
        "template_stage",
        f"cardinality(allowed_outcomes) <= {_MAX_OUTCOMES}",
    )

    op.add_column("task", sa.Column("outcome_index", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "outcome_only_on_cards",
        "task",
        "outcome_index IS NULL OR (parent_id IS NULL AND outcome_index >= 0)",
    )


def downgrade() -> None:
    op.drop_constraint("outcome_only_on_cards", "task", type_="check")
    op.drop_column("task", "outcome_index")

    op.drop_constraint("allowed_outcome_max_count", "template_stage", type_="check")
    op.drop_column("template_stage", "allowed_outcomes")

    op.drop_constraint("outcome_max_count", "board_column", type_="check")
    op.drop_column("board_column", "outcomes")
