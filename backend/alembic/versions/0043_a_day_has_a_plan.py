"""A day has a plan: the cards a person picked up for it, and their to-dos.

``day_pick`` (CYLIST-96) says a card is on one person's plan for one day;
``todo`` is one thing they mean to do about it, with ``@`` and ``>`` tags in its
text. A to-do imported from its card — a stage, a sub-task, a tick box —
carries ``source`` naming which, so ticking one in either place ticks the
other; ``day_pick.dismissed`` remembers the ones deleted from the plan, so
keeping the plan in step with the card does not bring them back. Wrapping a
day up marks what is left ``moved`` or ``not_done``, and a
moved to-do is copied onto the day it went to with ``carried_from`` set.

Two new tables and nothing changed on any old one, so there is nothing to
backfill and the downgrade only drops them.

Revision ID: 0043
Revises: 0042
Created: 2026-10-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_STATES = ("open", "done", "moved", "not_done")


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "day_pick",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column(
            "dismissed",
            postgresql.ARRAY(sa.String(length=80)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name=op.f("fk_day_pick_person_id_person"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name=op.f("fk_day_pick_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_day_pick")),
    )
    op.create_index(
        "ix_day_pick_person_id_task_id_day",
        "day_pick",
        ["person_id", "task_id", "day"],
        unique=True,
    )
    op.create_index("ix_day_pick_person_id_day", "day_pick", ["person_id", "day"])
    op.create_index(op.f("ix_day_pick_task_id"), "day_pick", ["task_id"])

    op.create_table(
        "todo",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("pick_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "state",
            sa.Enum(*_STATES, name="todo_state", native_enum=False, create_constraint=True),
            nullable=False,
        ),
        sa.Column("done_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("moved_to", sa.Date(), nullable=True),
        sa.Column("carried_from", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=80), nullable=True),
        *_timestamps(),
        sa.CheckConstraint("char_length(text) > 0", name=op.f("ck_todo_text_not_empty")),
        sa.CheckConstraint(
            "(state = 'moved') = (moved_to IS NOT NULL)",
            name=op.f("ck_todo_moved_says_where"),
        ),
        sa.ForeignKeyConstraint(
            ["pick_id"],
            ["day_pick.id"],
            name=op.f("fk_todo_pick_id_day_pick"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_todo")),
    )
    op.create_index(op.f("ix_todo_pick_id"), "todo", ["pick_id"])
    op.create_index(
        "ix_todo_pick_id_source",
        "todo",
        ["pick_id", "source"],
        unique=True,
        postgresql_where=sa.text("source IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_todo_pick_id_source", table_name="todo")
    op.drop_index(op.f("ix_todo_pick_id"), table_name="todo")
    op.drop_table("todo")
    op.drop_index(op.f("ix_day_pick_task_id"), table_name="day_pick")
    op.drop_index("ix_day_pick_person_id_day", table_name="day_pick")
    op.drop_index("ix_day_pick_person_id_task_id_day", table_name="day_pick")
    op.drop_table("day_pick")
