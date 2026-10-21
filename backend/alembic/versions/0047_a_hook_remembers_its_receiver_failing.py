"""A hook remembers that its receiver keeps failing, and is paused for it.

Two columns on ``hook`` (CYLIST-132), the courier's circuit breaker.

``consecutive_failures`` counts the attempts in a row, across every delivery
of the hook, that went unanswered or were refused; a 2xx puts it back to
nought. ``paused_until`` is when the courier may next send to the hook once
that count has crossed the threshold. Both start at rest everywhere: nothing
was counted before this revision, so every receiver begins presumed up.

Revision ID: 0047
Revises: 0046
Created: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hook",
        sa.Column(
            "consecutive_failures", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )
    op.add_column(
        "hook",
        sa.Column("paused_until", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("hook", "paused_until")
    op.drop_column("hook", "consecutive_failures")
