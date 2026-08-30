"""Credentials and audit trail.

The Phase 0 baseline: everything needed to authenticate a caller and record
what they did. Domain tables follow in later revisions.

Revision ID: 0001
Revises:
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "api_token",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("session", "api", name="token_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("scopes", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_api_token")),
    )
    op.create_index("ix_api_token_token_hash", "api_token", ["token_hash"], unique=True)
    op.create_index(
        "ix_api_token_kind_revoked_at", "api_token", ["kind", "revoked_at"], unique=False
    )

    op.create_table(
        "activity",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(length=120), nullable=False),
        sa.Column(
            "channel",
            sa.Enum("web", "api", name="channel", native_enum=False),
            nullable=False,
        ),
        sa.Column("verb", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["actor_token_id"],
            ["api_token.id"],
            name=op.f("fk_activity_actor_token_id_api_token"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_activity")),
    )
    op.create_index("ix_activity_occurred_at", "activity", ["occurred_at"], unique=False)
    op.create_index(
        "ix_activity_project_id_occurred_at",
        "activity",
        ["project_id", "occurred_at"],
        unique=False,
    )
    op.create_index(
        "ix_activity_entity_type_entity_id", "activity", ["entity_type", "entity_id"], unique=False
    )


def downgrade() -> None:
    op.drop_table("activity")
    op.drop_table("api_token")
