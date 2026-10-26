"""A to-do on the day's plan can be about no card.

A pick used to be a card on a day, and a to-do hung off one. A person with
something to do that belongs to no card had nowhere to write it. ``day_pick``
now carries its project itself (it used to be read through the card) and its
card may be null: that row is the person's *free* pick for the day, holding
the to-dos about no card. A partial unique index keeps it to one per person,
project and day.

``project_id`` is filled from each pick's card before it is made not null.

Revision ID: 0052
Revises: 0051
Created: 2026-10-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0052"
down_revision: str | None = "0051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("day_pick", sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.execute(
        "UPDATE day_pick SET project_id = task.project_id "
        "FROM task WHERE task.id = day_pick.task_id"
    )
    op.alter_column("day_pick", "project_id", nullable=False)
    op.create_foreign_key(
        op.f("fk_day_pick_project_id_project"),
        "day_pick",
        "project",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index(op.f("ix_day_pick_project_id"), "day_pick", ["project_id"])
    op.alter_column("day_pick", "task_id", nullable=True)
    op.create_index(
        "ix_day_pick_free",
        "day_pick",
        ["person_id", "project_id", "day"],
        unique=True,
        postgresql_where=sa.text("task_id IS NULL"),
    )


def downgrade() -> None:
    # A free pick has no card to go back to; it and its to-dos are dropped.
    op.execute("DELETE FROM day_pick WHERE task_id IS NULL")
    op.drop_index("ix_day_pick_free", table_name="day_pick")
    op.alter_column("day_pick", "task_id", nullable=False)
    op.drop_index(op.f("ix_day_pick_project_id"), table_name="day_pick")
    op.drop_constraint(op.f("fk_day_pick_project_id_project"), "day_pick", type_="foreignkey")
    op.drop_column("day_pick", "project_id")
