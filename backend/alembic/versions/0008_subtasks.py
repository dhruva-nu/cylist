"""Sub-tasks: cards under a card, and tick boxes on one.

Two kinds, because splitting work produces two different things. A piece big
enough to need an owner and a due date is a card of its own, numbered under its
parent — ``ATL-41-2`` — and drawn on the board like any other. A piece that is
only "and don't forget X" is a line of text with a tick box, which never earns
a card.

Three schema changes carry that:

* ``task.parent_id`` and ``task.sub_number``, with ``task.number`` becoming
  nullable — a sub-task borrows its parent's number rather than taking one of
  its own, so the unique index on ``(project_id, number)`` narrows to the
  top-level rows. A CHECK keeps the two schemes from ever mixing on one row.
* ``task.subtask_counter``, the same never-reused counter ``project`` has, so
  ``ATL-41-2`` cannot come back after the sub-task it named is deleted.
* ``task_checklist_item``, the tick boxes.

``TaskStatus`` also gains ``cancelled`` — "all sub-tasks complete or cancelled"
needs somewhere to record the second half. That costs no DDL: the status enum
is stored as its values in a VARCHAR with no CHECK behind it, precisely so
gaining a member is a code change rather than a migration — bar the column
being one character wider, since 'cancelled' is longer than 'blocked'. The
downgrade still has to deal with the rows: the older code does not know the
word.

Revision ID: 0008
Revises: 0007
Created: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

_STATUSES = ("active", "hold", "blocked", "cancelled")

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("task", sa.Column("parent_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("task", sa.Column("sub_number", sa.Integer(), nullable=True))
    op.add_column(
        "task",
        sa.Column("subtask_counter", sa.Integer(), server_default=sa.text("0"), nullable=False),
    )
    op.alter_column("task", "number", existing_type=sa.Integer(), nullable=True)

    op.create_foreign_key(
        op.f("fk_task_parent_id_task"),
        "task",
        "task",
        ["parent_id"],
        ["id"],
        ondelete="CASCADE",
    )

    # Narrowed to the top-level rows: a sub-task holds no project number, and
    # NULLs would slip past a plain unique index in any case.
    op.drop_index("ix_task_project_id_number", table_name="task")
    op.create_index(
        "ix_task_project_id_number",
        "task",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
    op.create_index(
        "ix_task_parent_id_sub_number", "task", ["parent_id", "sub_number"], unique=True
    )
    op.create_check_constraint(
        "numbered_by_parentage",
        "task",
        "(parent_id IS NULL) = (number IS NOT NULL) AND (parent_id IS NULL) = (sub_number IS NULL)",
    )

    # Nine characters, because 'cancelled' is longer than 'blocked'. A
    # non-native enum is a VARCHAR sized to its longest member.
    op.alter_column(
        "task",
        "status",
        existing_type=sa.String(length=7),
        type_=sa.Enum(*_STATUSES, name="task_status", native_enum=False),
        existing_nullable=False,
    )

    op.create_table(
        "task_checklist_item",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column(
            "state",
            sa.Enum("open", "done", "cancelled", name="checklist_state", native_enum=False),
            nullable=False,
        ),
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
            ["task_id"],
            ["task.id"],
            name=op.f("fk_task_checklist_item_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_task_checklist_item")),
    )
    op.create_index(
        "ix_task_checklist_item_task_id_position", "task_checklist_item", ["task_id", "position"]
    )


def downgrade() -> None:
    op.drop_index("ix_task_checklist_item_task_id_position", table_name="task_checklist_item")
    op.drop_table("task_checklist_item")

    # Sub-tasks cannot be represented once the columns are gone, and 'cancelled'
    # is a word the older code does not know. Both are put back to something
    # the old schema can hold rather than left to trip over later.
    op.execute(sa.text("DELETE FROM task WHERE parent_id IS NOT NULL"))
    op.execute(sa.text("UPDATE task SET status = 'hold' WHERE status = 'cancelled'"))
    op.alter_column(
        "task",
        "status",
        existing_type=sa.String(length=9),
        type_=sa.Enum(*_STATUSES[:-1], name="task_status", native_enum=False),
        existing_nullable=False,
    )

    op.drop_constraint("numbered_by_parentage", "task", type_="check")
    op.drop_index("ix_task_parent_id_sub_number", table_name="task")
    op.drop_index("ix_task_project_id_number", table_name="task")
    op.create_index("ix_task_project_id_number", "task", ["project_id", "number"], unique=True)

    op.drop_constraint(op.f("fk_task_parent_id_task"), "task", type_="foreignkey")
    op.alter_column("task", "number", existing_type=sa.Integer(), nullable=False)
    op.drop_column("task", "subtask_counter")
    op.drop_column("task", "sub_number")
    op.drop_column("task", "parent_id")
