"""A to-do handed on at wrap-up can say why.

``moved_reason`` is optional free text, kept on the moved to-do and on its
copy so the reason reads on either day and in the day report.

Revision ID: 0054
Revises: 0053
Created: 2026-11-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054"
down_revision: str | None = "0053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("todo", sa.Column("moved_reason", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("todo", "moved_reason")
