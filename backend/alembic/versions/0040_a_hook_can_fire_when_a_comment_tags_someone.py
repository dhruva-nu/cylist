"""A hook can fire only when a comment tags somebody.

Two more filters on a hook (CYLIST-94): ``mentions_only`` fires it only for a
comment that tags somebody with ``@``, and ``mentioned_person_id`` only for one
that tags that person. The boolean defaults to ``false`` and the id to null, so
every existing hook keeps firing on every comment it did before.

Revision ID: 0040
Revises: 0039
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0040"
down_revision: str | None = "0039"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "hook",
        sa.Column("mentions_only", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.add_column("hook", sa.Column("mentioned_person_id", postgresql.UUID(as_uuid=True)))


def downgrade() -> None:
    op.drop_column("hook", "mentioned_person_id")
    op.drop_column("hook", "mentions_only")
