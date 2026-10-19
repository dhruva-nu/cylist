"""A phone can be told: the devices that receive agent pushes.

``push_device`` (CYLIST-107) holds one Firebase Cloud Messaging registration
token per phone install, against the person it is signed in as and the API
token it signed in with. The server pushes to these when an agent session on
a card changes state, so the Android app can show it as a Live Update while it
is closed.

One new table and nothing changed on any old one, so there is nothing to
backfill and the downgrade only drops it.

Revision ID: 0045
Revises: 0044
Created: 2026-10-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0045"
down_revision: str | None = "0044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "push_device",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "platform",
            sa.Enum(
                "android",
                name="device_platform",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("last_registered_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_push_device_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["api_token.id"],
            name=op.f("fk_push_device_token_id_api_token"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_push_device")),
    )
    op.create_index("ix_push_device_fcm_token", "push_device", ["fcm_token"], unique=True)
    op.create_index("ix_push_device_person_id", "push_device", ["person_id"])
    op.create_index("ix_push_device_token_id", "push_device", ["token_id"])


def downgrade() -> None:
    op.drop_index("ix_push_device_token_id", table_name="push_device")
    op.drop_index("ix_push_device_person_id", table_name="push_device")
    op.drop_index("ix_push_device_fcm_token", table_name="push_device")
    op.drop_table("push_device")
