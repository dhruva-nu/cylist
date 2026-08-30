"""Projects and people.

Adds the two top-level domain tables and the join between them, and finally
points ``activity.project_id`` at a real table now that one exists.

Revision ID: 0002
Revises: 0001
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "person",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("team", "client", name="person_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("role", sa.String(length=160), nullable=False),
        sa.Column("responsibilities", sa.Text(), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=True),
        sa.Column("colour", sa.String(length=7), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_person")),
    )
    op.create_index("ix_person_kind_archived_at", "person", ["kind", "archived_at"], unique=False)

    op.create_table(
        "project",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=6), nullable=False),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=False),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project")),
    )
    op.create_index("ix_project_key", "project", ["key"], unique=True)
    op.create_index("ix_project_archived_at", "project", ["archived_at"], unique=False)

    op.create_table(
        "project_member",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            name=op.f("fk_project_member_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_project_member_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("project_id", "person_id", name=op.f("pk_project_member")),
    )

    op.create_foreign_key(
        op.f("fk_activity_project_id_project"),
        "activity",
        "project",
        ["project_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_activity_project_id_project"), "activity", type_="foreignkey")
    op.drop_table("project_member")
    op.drop_index("ix_project_archived_at", table_name="project")
    op.drop_index("ix_project_key", table_name="project")
    op.drop_table("project")
    op.drop_index("ix_person_kind_archived_at", table_name="person")
    op.drop_table("person")
