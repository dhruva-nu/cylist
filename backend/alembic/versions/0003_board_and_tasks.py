"""The Kanban board.

Adds columns, tasks, the people a stalled task is waiting on, and the timeline
that holds both comments and status changes. Also puts a task counter on
``project``: numbers are allocated from it rather than from ``MAX(number)`` so
that deleting a card never puts its reference back into circulation.

Revision ID: 0003
Revises: 0002
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "project",
        sa.Column("task_counter", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    op.create_table(
        "board_column",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
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
            name=op.f("fk_board_column_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_board_column")),
    )
    op.create_index(
        "ix_board_column_project_id_position", "board_column", ["project_id", "position"]
    )

    op.create_table(
        "task",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column(
            "type",
            sa.Enum("feature", "bug", "chore", name="task_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("assignee_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("active", "hold", "blocked", name="task_status", native_enum=False),
            nullable=False,
        ),
        sa.Column("jira_ref", sa.String(length=64), nullable=True),
        sa.Column("pr_ref", sa.String(length=200), nullable=True),
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
            name=op.f("fk_task_project_id_project"),
            ondelete="CASCADE",
        ),
        # No ON DELETE on these two: NO ACTION is checked at the end of the
        # statement, so dropping a project still cascades cleanly while a bare
        # delete of a column or a person that tasks point at is refused.
        sa.ForeignKeyConstraint(
            ["column_id"], ["board_column.id"], name=op.f("fk_task_column_id_board_column")
        ),
        sa.ForeignKeyConstraint(
            ["assignee_id"], ["person.id"], name=op.f("fk_task_assignee_id_person")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task")),
    )
    op.create_index("ix_task_project_id_number", "task", ["project_id", "number"], unique=True)
    op.create_index(
        "ix_task_project_id_column_id_position", "task", ["project_id", "column_id", "position"]
    )

    op.create_table(
        "task_waiting_on",
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            ["task_id"],
            ["task.id"],
            name=op.f("fk_task_waiting_on_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_task_waiting_on_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("task_id", "person_id", name=op.f("pk_task_waiting_on")),
    )

    op.create_table(
        "task_comment",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum("comment", "status_change", name="comment_kind", native_enum=False),
            nullable=False,
        ),
        sa.Column("meta", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
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
            ["task_id"],
            ["task.id"],
            name=op.f("fk_task_comment_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["person.id"],
            name=op.f("fk_task_comment_author_id_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_comment")),
    )
    op.create_index("ix_task_comment_task_id_created_at", "task_comment", ["task_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_task_comment_task_id_created_at", table_name="task_comment")
    op.drop_table("task_comment")
    op.drop_table("task_waiting_on")
    op.drop_index("ix_task_project_id_column_id_position", table_name="task")
    op.drop_index("ix_task_project_id_number", table_name="task")
    op.drop_table("task")
    op.drop_index("ix_board_column_project_id_position", table_name="board_column")
    op.drop_table("board_column")
    op.drop_column("project", "task_counter")
