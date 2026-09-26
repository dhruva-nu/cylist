"""Indexes shaped like the queries that use them.

Four reads on ``task`` could not use the index that was meant for them, which
``EXPLAIN`` says plainly and the size of a demo board hid:

* **A card by its reference.** ``ATL-41`` is found by project key and number
  on every CLI, MCP and link lookup, and ``ix_task_project_id_number`` was
  partial on ``parent_id IS NULL``. The lookup does not repeat that predicate,
  so the planner could not use the index and filtered the whole project
  instead. The predicate was never needed: a sub-task's number is NULL — the
  ``numbered_by_parentage`` check says so — and NULLs do not collide, so the
  plain index is unique over exactly the same rows.
* **A column top to bottom.** Every create, move and renumber reads
  ``WHERE column_id = ? ORDER BY position``, and so does the check that stops a
  column holding cards from being deleted. The index was
  ``(project_id, column_id, position)``, and the query names no project — a
  column already belongs to one — so it walked the whole index and sorted.
  It becomes ``(column_id, position)``. Nothing is lost: the reads by project
  now have the reference index above, which leads with ``project_id`` and,
  no longer partial, covers every row.
* **A goal's cards,** counted for its progress wherever a goal is shown and
  found by ``ON DELETE SET NULL`` when one is deleted. There was no index on
  ``goal_id`` at all, and the progress query names no project either.
* **A template's cards,** counted when deciding whether it may be deleted and
  checked by its ``NO ACTION`` key when it is. No index either.

The last two are partial on ``IS NOT NULL``: most cards carry neither, and an
index of NULLs answers nothing.

No row changes, and the downgrade puts each index back as it was.

Revision ID: 0030
Revises: 0029
Created: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_index("ix_task_project_id_number", table_name="task")
    op.create_index("ix_task_project_id_number", "task", ["project_id", "number"], unique=True)

    op.drop_index("ix_task_project_id_column_id_position", table_name="task")
    op.create_index("ix_task_column_id_position", "task", ["column_id", "position"])

    op.create_index(
        "ix_task_goal_id", "task", ["goal_id"], postgresql_where=sa.text("goal_id IS NOT NULL")
    )
    op.create_index(
        "ix_task_template_id",
        "task",
        ["template_id"],
        postgresql_where=sa.text("template_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_task_template_id", table_name="task")
    op.drop_index("ix_task_goal_id", table_name="task")

    op.drop_index("ix_task_column_id_position", table_name="task")
    op.create_index(
        "ix_task_project_id_column_id_position", "task", ["project_id", "column_id", "position"]
    )

    op.drop_index("ix_task_project_id_number", table_name="task")
    op.create_index(
        "ix_task_project_id_number",
        "task",
        ["project_id", "number"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
