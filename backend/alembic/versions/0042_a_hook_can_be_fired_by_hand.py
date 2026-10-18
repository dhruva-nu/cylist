"""A hook can be one that only fires when somebody fires it.

A manual hook (CYLIST-95) never fires on a board's changes: it is a URL with a
button on the Hooks page — "deploy now", "rebuild the docs" — POSTed a
``hook.fired`` event when an admin presses it. Defaults to ``false``, so every
existing hook keeps firing on the changes it did before.

Revision ID: 0042
Revises: 0041
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hook",
        sa.Column("manual", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("hook", "manual")
