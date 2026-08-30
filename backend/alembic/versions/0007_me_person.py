"""Mark one person in the directory as you.

``person.is_me`` answers "who is the owner?" — the person put on every project
as it is created. The unique index covers only the rows where the flag is true,
so the schema itself holds the "at most one" that would otherwise be a rule the
service had to keep remembering.

There is no back-fill: nothing in an existing directory says which entry is the
owner, and guessing wrong would quietly put the wrong person on every new
project. The first person marked in the app takes it.

Revision ID: 0007
Revises: 0006
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "person",
        sa.Column("is_me", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_index(
        "ix_person_is_me",
        "person",
        ["is_me"],
        unique=True,
        postgresql_where=sa.text("is_me"),
    )


def downgrade() -> None:
    op.drop_index("ix_person_is_me", table_name="person")
    op.drop_column("person", "is_me")
