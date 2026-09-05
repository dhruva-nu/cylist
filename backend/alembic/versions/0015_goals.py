"""Goals: the epic a card belongs to, and the colour it wears on the board.

One new table, one new counter on ``project``, one new column on ``task``.
Nothing is back-filled: every existing card gets a null ``goal_id``, which is
"this card stands on its own" rather than "unclassified" — see
``app.models.goal``. A board that had no goals yesterday behaves identically
today, and its cards keep drawing their status colour on the rail.

``task.goal_id`` is ``ON DELETE SET NULL`` where ``task.template_id`` has no
``ondelete`` at all, and the difference is deliberate: a template is a rule
about where a card may go and cannot be pulled out from under one, while a
goal is a label saying what the card is for. Deleting the label leaves the
card.

Revision ID: 0015
Revises: 0014
Created: 2026-09-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

_STATUSES = ("open", "achieved", "dropped")

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "goal",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=False),
        sa.Column(
            "status",
            sa.Enum(*_STATUSES, name="goal_status", native_enum=False),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("achieved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("target_date", sa.Date(), nullable=True),
        sa.Column("owner_id", postgresql.UUID(as_uuid=True), nullable=False),
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
            name=op.f("fk_goal_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(["owner_id"], ["person.id"], name=op.f("fk_goal_owner_id_person")),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_goal")),
    )
    op.create_index("ix_goal_project_id_number", "goal", ["project_id", "number"], unique=True)
    op.create_index("ix_goal_project_id_name", "goal", ["project_id", "name"], unique=True)

    op.add_column(
        "project",
        sa.Column("goal_counter", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )

    op.add_column("task", sa.Column("goal_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        op.f("fk_task_goal_id_goal"),
        "task",
        "goal",
        ["goal_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "goal_only_on_cards",
        "task",
        "goal_id IS NULL OR parent_id IS NULL",
    )


def downgrade() -> None:
    op.drop_constraint("goal_only_on_cards", "task", type_="check")
    op.drop_constraint(op.f("fk_task_goal_id_goal"), "task", type_="foreignkey")
    op.drop_column("task", "goal_id")

    op.drop_column("project", "goal_counter")

    op.drop_index("ix_goal_project_id_name", table_name="goal")
    op.drop_index("ix_goal_project_id_number", table_name="goal")
    op.drop_table("goal")
