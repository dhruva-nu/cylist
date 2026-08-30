"""The vault: trees, nodes and encrypted secrets.

Only ``vault_secret.secret_ciphertext`` holds anything encrypted. Username,
URL and notes are plaintext on purpose so the vault stays searchable — see
``app.models.vault`` for the reasoning.

Two constraints in here are doing more work than they look:

* ``uq_vault_node_tree_id_parent_id_name`` is ``NULLS NOT DISTINCT`` (Postgres
  15+). Without it a NULL ``parent_id`` would make every top-level node unique
  by definition and the constraint would silently permit duplicates.
* ``vault_secret`` carries a ``node_kind`` column pinned to ``'secret'`` by a
  CHECK, and references ``vault_node (id, kind)`` rather than just ``id``.
  That is what makes it impossible to attach a credential to a branch.

Revision ID: 0005
Revises: 0002
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0005"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NODE_KIND = sa.Enum("branch", "secret", name="vault_node_kind", native_enum=False)


def upgrade() -> None:
    op.create_table(
        "vault_tree",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
            name=op.f("fk_vault_tree_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vault_tree")),
        sa.UniqueConstraint("project_id", "name", name=op.f("uq_vault_tree_project_id_name")),
    )
    op.create_index(
        "ix_vault_tree_project_id_position", "vault_tree", ["project_id", "position"], unique=False
    )

    op.create_table(
        "vault_node",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tree_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("kind", _NODE_KIND, nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
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
            ["tree_id"],
            ["vault_tree.id"],
            name=op.f("fk_vault_node_tree_id_vault_tree"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_id"],
            ["vault_node.id"],
            name=op.f("fk_vault_node_parent_id_vault_node"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_vault_node")),
        sa.UniqueConstraint(
            "tree_id",
            "parent_id",
            "name",
            name=op.f("uq_vault_node_tree_id_parent_id_name"),
            postgresql_nulls_not_distinct=True,
        ),
        sa.UniqueConstraint("id", "kind", name=op.f("uq_vault_node_id_kind")),
    )
    op.create_index(
        "ix_vault_node_tree_id_parent_id_position",
        "vault_node",
        ["tree_id", "parent_id", "position"],
        unique=False,
    )

    op.create_table(
        "vault_secret",
        sa.Column("node_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("node_kind", _NODE_KIND, nullable=False),
        sa.Column("username", sa.String(length=254), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
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
            "node_kind = 'secret'", name=op.f("ck_vault_secret_node_kind_is_secret")
        ),
        sa.ForeignKeyConstraint(
            ["node_id", "node_kind"],
            ["vault_node.id", "vault_node.kind"],
            name=op.f("fk_vault_secret_node_id_node_kind_vault_node"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("node_id", name=op.f("pk_vault_secret")),
    )


def downgrade() -> None:
    op.drop_table("vault_secret")
    op.drop_index("ix_vault_node_tree_id_parent_id_position", table_name="vault_node")
    op.drop_table("vault_node")
    op.drop_index("ix_vault_tree_project_id_position", table_name="vault_tree")
    op.drop_table("vault_tree")
