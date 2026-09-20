"""Access that knows which column, which upload, and which goal.

Revision 0025 gave a role a set of permissions, one yes-or-no per area of the
product. This makes three of those answers specific to the thing they are
about, which is what an admin actually wants to say: not "may they move cards"
but "may they move cards into Done"; not "may they read the vault" but "may
they read *this*".

**What arrives.**

* ``role_column_rule`` — per role, per column, ``may_enter`` and ``may_stage``.
* ``role_clearance`` — per role, the most sensitive thing it may read.
* ``sensitivity`` on ``file_item`` and ``vault_node``, and a
  ``default_sensitivity`` on ``folder`` and ``vault_tree`` that new children
  inherit.
* Two more permissions in the vocabulary ``project_permission`` stores:
  ``goal_assign`` (which goal a card is on) and ``goal_owner`` (whose goal a
  goal is), split off ``goals`` — which keeps the rest.

**The two new tables are restrictions, and the one from 0025 is a grant.**
That is the layering rather than an inconsistency: a flat permission decides
whether a role may touch cards or read uploads at all, and these only narrow
it. So an absent row means unrestricted, in both of them, and nothing here is
back-filled. It also survives a board changing shape — a column added next
month is open to everybody who may move cards, where a grant-shaped table
would have locked every role out of it until an admin noticed.

**Everything already stored comes through unchanged.** Every upload is
``internal`` and every role is cleared to ``restricted``, so the same people
see the same files the day after this runs. A deployment that wanted otherwise
would have had to say so, and there was nowhere to say it until now.

**The two new permissions are granted wherever ``goals`` was.** A role that
could change goals could also put cards on them and hand them over, because
until this revision those were the same permission. Splitting them and
granting only the narrow one would have taken something away from somebody
without being asked to.

**The downgrade folds the split back** — a role keeps ``goals`` if it had any
of the three — drops the two tables, and drops the levels. A board that comes
back through it is a board where every upload is readable again.

Revision ID: 0026
Revises: 0025
Created: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DEFAULT_LEVEL = "internal"
"""Mirrors ``app.core.sensitivity.DEFAULT_LEVEL``. Spelled out rather than
imported, for the reason 0025 spells out its vocabulary: this records what the
column defaulted to when the revision ran."""

_SPLIT_FROM_GOALS = ("goal_assign", "goal_owner")


def upgrade() -> None:
    op.create_table(
        "role_column_rule",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("column_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("may_enter", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("may_stage", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
            name=op.f("fk_role_column_rule_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["column_id"],
            ["board_column.id"],
            name=op.f("fk_role_column_rule_column_id_board_column"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_role_column_rule_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_column_rule")),
    )
    op.create_index(
        "ix_role_column_rule_role_id_column_id",
        "role_column_rule",
        ["role_id", "column_id"],
        unique=True,
        postgresql_where=sa.text("role_id IS NOT NULL"),
    )
    op.create_index(
        "ix_role_column_rule_project_id_column_id",
        "role_column_rule",
        ["project_id", "column_id"],
        unique=True,
        postgresql_where=sa.text("role_id IS NULL"),
    )

    op.create_table(
        "role_clearance",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("level", sa.String(length=16), nullable=False),
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
            name=op.f("fk_role_clearance_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_role_clearance_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_role_clearance")),
    )
    op.create_index(
        "ix_role_clearance_role_id",
        "role_clearance",
        ["role_id"],
        unique=True,
        postgresql_where=sa.text("role_id IS NOT NULL"),
    )
    op.create_index(
        "ix_role_clearance_project_id",
        "role_clearance",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("role_id IS NULL"),
    )

    for table, column in (
        ("file_item", "sensitivity"),
        ("folder", "default_sensitivity"),
        ("vault_node", "sensitivity"),
        ("vault_tree", "default_sensitivity"),
    ):
        op.add_column(
            table,
            sa.Column(
                column,
                sa.String(length=16),
                server_default=sa.text(f"'{_DEFAULT_LEVEL}'"),
                nullable=False,
            ),
        )

    _split_the_goals_permission()


def _split_the_goals_permission() -> None:
    """Give the two new permissions to every role that already had ``goals``.

    Until this revision, putting a card on a goal and handing a goal to
    somebody else *were* the ``goals`` permission. Splitting them out and
    granting only the narrow one would take two things away from everybody who
    has the wide one, which is not what splitting a word into three is for.
    """
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO project_permission (id, project_id, role_id, permission)
            SELECT gen_random_uuid(), held.project_id, held.role_id, wanted.permission
              FROM project_permission AS held
              CROSS JOIN unnest(CAST(:added AS text[])) AS wanted(permission)
             WHERE held.permission = 'goals'
            """
        ),
        {"added": list(_SPLIT_FROM_GOALS)},
    )


def downgrade() -> None:
    # Fold the split back before the rows that record it are gone: a role that
    # held any of the three held `goals` under the old, wider word.
    connection = op.get_bind()
    connection.execute(
        sa.text(
            """
            DELETE FROM project_permission
             WHERE permission = ANY(CAST(:added AS text[]))
            """
        ),
        {"added": list(_SPLIT_FROM_GOALS)},
    )

    for table, column in (
        ("vault_tree", "default_sensitivity"),
        ("vault_node", "sensitivity"),
        ("folder", "default_sensitivity"),
        ("file_item", "sensitivity"),
    ):
        op.drop_column(table, column)

    op.drop_index("ix_role_clearance_project_id", table_name="role_clearance")
    op.drop_index("ix_role_clearance_role_id", table_name="role_clearance")
    op.drop_table("role_clearance")

    op.drop_index("ix_role_column_rule_project_id_column_id", table_name="role_column_rule")
    op.drop_index("ix_role_column_rule_role_id_column_id", table_name="role_column_rule")
    op.drop_table("role_column_rule")
