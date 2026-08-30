"""Files: folders, items and content-addressed blobs.

Revision ID: 0004
Revises: 0003
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "blob",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("path", sa.Text(), nullable=False),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_blob")),
    )
    op.create_index("ix_blob_sha256", "blob", ["sha256"], unique=True)

    op.create_table(
        "folder",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
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
            name=op.f("fk_folder_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["folder.id"],
            name=op.f("fk_folder_parent_id_folder"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_folder")),
    )
    # Partial, because Postgres counts NULLs as distinct: without the split, a
    # project could hold two top-level folders with the same name.
    op.create_index(
        "ix_folder_project_id_name",
        "folder",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_folder_parent_id_name",
        "folder",
        ["parent_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NOT NULL"),
    )
    op.create_index(
        "ix_folder_project_id_parent_id", "folder", ["project_id", "parent_id"], unique=False
    )

    op.create_table(
        "file_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("folder_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("file", "link", name="item_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "upload", "sharepoint", "gdrive", "other", name="item_source", native_enum=False
            ),
            nullable=False,
        ),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("mime", sa.String(length=255), nullable=True),
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
        sa.CheckConstraint(
            "(kind = 'file' AND blob_id IS NOT NULL AND url IS NULL)"
            " OR (kind = 'link' AND url IS NOT NULL AND blob_id IS NULL)",
            name=op.f("ck_file_item_kind_matches_content"),
        ),
        sa.ForeignKeyConstraint(
            ["folder_id"],
            ["folder.id"],
            name=op.f("fk_file_item_folder_id_folder"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["blob.id"],
            name=op.f("fk_file_item_blob_id_blob"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["person.id"],
            name=op.f("fk_file_item_added_by_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_file_item")),
    )
    op.create_index("ix_file_item_folder_id_name", "file_item", ["folder_id", "name"], unique=True)
    op.create_index("ix_file_item_blob_id", "file_item", ["blob_id"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_file_item_blob_id", table_name="file_item")
    op.drop_index("ix_file_item_folder_id_name", table_name="file_item")
    op.drop_table("file_item")
    op.drop_index("ix_folder_project_id_parent_id", table_name="folder")
    op.drop_index("ix_folder_parent_id_name", table_name="folder")
    op.drop_index("ix_folder_project_id_name", table_name="folder")
    op.drop_table("folder")
    op.drop_index("ix_blob_sha256", table_name="blob")
    op.drop_table("blob")
