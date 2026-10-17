"""A hook can fire only for cards assigned to the agent.

``hook.agent_only`` is one more filter on the card (CYLIST-92): when set, a
change fires the hook only if the card's owner is the agent person. A boolean
with a server default of ``false``, so every existing hook keeps matching
whoever owns the card.

Revision ID: 0039
Revises: 0038
Created: 2026-10-16
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hook",
        sa.Column("agent_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("hook", "agent_only")
