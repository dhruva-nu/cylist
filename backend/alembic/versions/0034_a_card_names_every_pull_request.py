"""A card names every pull request that carries it, not just the last one.

``task.pr_ref`` held one reference, so a card whose work landed as a backend
pull request and a frontend one could only record whichever was pasted in
second. It becomes ``task.pr_refs``, a text array in the same shape as
``task.sub_statuses`` and ``board_column.outcomes`` — short strings owned
entirely by the row, read on every board query, pointed at from nowhere. Rows
of their own would have bought another SELECT on the board's hottest path and
nothing else.

Nothing is lost on the way up: every card that named a pull request keeps it,
back-filled as a one-element array, and every card that named none gets the
empty array that is now how "no pull request" is spelled. The column is
``NOT NULL`` with a ``'{}'`` default, so there is one way to say it rather than
two.

``ck_task_pr_ref_max_count`` caps a card at 20 references. Not a number anybody
should reach — a card that took twenty pull requests was more than one card —
but a ceiling the database holds for every writer, the way
``ck_board_column_outcome_max_count`` does for a column's outcomes.

The downgrade restores the single ``pr_ref`` from the first entry of each list,
and refuses outright if any card names more than one, because putting the rest
somewhere is not something this revision can invent — the same answer 0009 and
0012 give when reversing would have to make something up.

Revision ID: 0034
Revises: 0033
Created: 2026-09-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_PR_REFS = 20
"""Kept here rather than imported from the model: a revision has to keep
meaning what it meant, and the model's ceiling may move."""


def upgrade() -> None:
    op.add_column(
        "task",
        sa.Column(
            "pr_refs",
            postgresql.ARRAY(sa.String(length=200)),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )

    # Added before the column is dropped, so no row is ever without its
    # reference: a card that named one names it in both shapes for the length
    # of this statement, and in the new one afterwards.
    op.execute(
        sa.text(
            "UPDATE task SET pr_refs = ARRAY[pr_ref] "
            "WHERE pr_ref IS NOT NULL AND btrim(pr_ref) <> ''"
        )
    )

    op.drop_column("task", "pr_ref")

    op.create_check_constraint(
        "pr_ref_max_count",
        "task",
        f"cardinality(pr_refs) <= {MAX_PR_REFS}",
    )


def downgrade() -> None:
    crowded = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM task WHERE cardinality(pr_refs) > 1"))
        .scalar_one()
    )
    if crowded:
        raise RuntimeError(
            f"{crowded} card(s) name more than one pull request, which revision 0033 "
            "cannot represent — it has room for one. Reduce them to one each before "
            "downgrading, or the rest would be dropped without anybody choosing which."
        )

    # The bare name, not the stored `ck_task_pr_ref_max_count`: NAMING_CONVENTION
    # is applied on the way out, so spelling the prefix here produces
    # `ck_task_ck_task_pr_ref_max_count` and finds nothing.
    op.drop_constraint("pr_ref_max_count", "task", type_="check")
    op.add_column("task", sa.Column("pr_ref", sa.String(length=200), nullable=True))
    # Postgres arrays are one-based; an empty array subscripts to NULL, which is
    # exactly the "no pull request" this column spells.
    op.execute(sa.text("UPDATE task SET pr_ref = pr_refs[1]"))
    op.drop_column("task", "pr_refs")
