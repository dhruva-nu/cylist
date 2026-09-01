"""Widen ``task.jira_ref`` to hold a URL, like ``task.pr_ref`` already does.

Both ref fields are free text, and people fill them either way: the key typed
by hand, or the whole URL pasted off the browser bar. ``pr_ref`` was given 200
characters for exactly that reason; ``jira_ref`` was left at 64, which fits
``ATL-41`` and a plain ``/browse/`` link but not the deep link a Jira board
hands out — ``…/jira/software/projects/ATL/boards/2?selectedIssue=ATL-41`` is
past 64 on its own, before the company's own hostname. A paste that long came
back as a 422 on a field documented to take a URL.

Widening a VARCHAR is a catalogue-only change in Postgres: no table rewrite, no
lock worth naming. Narrowing it back is not, which is what ``downgrade`` has to
be careful about.

Revision ID: 0009
Revises: 0008
Created: 2026-09-01
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "task",
        "jira_ref",
        existing_type=sa.String(length=64),
        type_=sa.String(length=200),
        existing_nullable=True,
    )


def downgrade() -> None:
    # Truncating someone's Jira link to 64 characters loses the link, and the
    # halves that survive would be indistinguishable from a key. Refuse instead
    # and say what to do, as revision 0006 does for the same reason.
    too_long = (
        op.get_bind()
        .execute(sa.text("SELECT count(*) FROM task WHERE length(jira_ref) > 64"))
        .scalar_one()
    )
    if too_long:
        raise RuntimeError(
            f"{too_long} task(s) have a jira_ref longer than the 64 characters revision "
            "0008 allows. Shorten them to the issue key before downgrading."
        )

    op.alter_column(
        "task",
        "jira_ref",
        existing_type=sa.String(length=200),
        type_=sa.String(length=64),
        existing_nullable=True,
    )
