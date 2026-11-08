"""Images in a card's description live in the blob store.

``description_image`` points at ``blob`` with ``ON DELETE RESTRICT``, as
``file_item`` and ``skill`` do; a description refers to a row here by id, so
the bytes never enter the ``task`` table.

Revision ID: 0053
Revises: 0052
Created: 2026-11-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0053"
down_revision: str | None = "0052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "description_image",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
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
            ["project_id"],
            ["project.id"],
            name=op.f("fk_description_image_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["blob.id"],
            name=op.f("fk_description_image_blob_id_blob"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["person.id"],
            name=op.f("fk_description_image_added_by_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_description_image")),
    )
    op.create_index("ix_description_image_project_id", "description_image", ["project_id"])
    op.create_index("ix_description_image_blob_id", "description_image", ["blob_id"])


def downgrade() -> None:
    op.drop_index("ix_description_image_blob_id", table_name="description_image")
    op.drop_index("ix_description_image_project_id", table_name="description_image")
    op.drop_table("description_image")
