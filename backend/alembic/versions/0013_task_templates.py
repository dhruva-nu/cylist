"""Task templates: what kind of card a task is, which columns it may sit in,
and the sub-stages it passes through in each before it can move on.

Two new tables and one new column. Nothing is back-filled: every existing
card gets a null ``template_id``, which is exactly "no template governs this"
— see ``app.models.template``. A board that had no templates yesterday
behaves identically today.

``template_stage`` cascades from both ``task_template`` and ``board_column``:
deleting a template takes its own stages with it, and deleting a column takes
it out of every template that named it, rather than leaving a stage pointing
at a column that no longer exists.

Revision ID: 0013
Revises: 0012
Created: 2026-09-04
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "task_template",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
            name=op.f("fk_task_template_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_template")),
    )
    op.create_index(
        "ix_task_template_project_id_name", "task_template", ["project_id", "name"], unique=True
    )

    op.create_table(
        "template_stage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "sub_stage_labels",
            postgresql.ARRAY(sa.String(length=60)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
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
            ["template_id"],
            ["task_template.id"],
            name=op.f("fk_template_stage_template_id_task_template"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["board_column.id"],
            name=op.f("fk_template_stage_column_id_board_column"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_template_stage")),
    )
    op.create_index(
        "ix_template_stage_template_id_column_id",
        "template_stage",
        ["template_id", "column_id"],
        unique=True,
    )
    op.create_check_constraint(
        "sub_stage_max_count",
        "template_stage",
        "cardinality(sub_stage_labels) <= 4",
    )

    op.add_column("task", sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_task_template_id_task_template"),
        "task",
        "task_template",
        ["template_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(op.f("fk_task_template_id_task_template"), "task", type_="foreignkey")
    op.drop_column("task", "template_id")

    op.drop_constraint("sub_stage_max_count", "template_stage", type_="check")
    op.drop_index("ix_template_stage_template_id_column_id", table_name="template_stage")
    op.drop_table("template_stage")

    op.drop_index("ix_task_template_project_id_name", table_name="task_template")
    op.drop_table("task_template")
