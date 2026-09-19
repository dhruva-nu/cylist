"""A role says what its holders may do, and the admin says what that is.

CYLIST-45 gave a project roles and said, in as many words, that a role permits
nothing — it was a name an admin hands out, and the only authority any of them
carried was the admin role's own. This is the other half of that sentence.

**What arrives.** ``project_permission``: one row per role per thing the role
allows, from the fixed vocabulary in :mod:`app.auth.permissions`. A row whose
``role_id`` is NULL is the project's baseline — what somebody here with no
role may do, which is most of a board most of the time, and what somebody not
on the project at all may do, since membership has never been a fence.

**Nothing is stored for the admin role.** It holds everything by being the
admin role. Rows saying so would be rows somebody could delete, and the way
back from a board whose admin cannot administer it is a support ticket.

**The back-fill grants everything to everyone.** Every project comes through
this revision permitting exactly what it permitted before it: every existing
role, and the role-less baseline, gets the full set. An upgrade that quietly
fenced boards which had never been fenced would be a data migration with an
opinion, and the opinion is the admin's — the screen is where they narrow it.

**The unique indexes are partial, in two halves.** Postgres counts NULLs as
distinct, so one constraint over ``(role_id, permission)`` would leave the
baseline rows — the ones whose ``role_id`` is NULL — free to duplicate, which
is exactly the set a double-clicked checkbox would double up.

**The foreign key is the composite, deferred one** ``project_member`` already
carries; the note in that revision explains both halves, and they apply here
unchanged. It is satisfied trivially for a baseline row, because a composite
key under MATCH SIMPLE holds the moment any of its columns is null.

**The downgrade drops the table**, which is every grant. Nothing else moves:
roles, membership and boards come through it unchanged, and a board that comes
back through is a board where everybody may do everything again.

Revision ID: 0025
Revises: 0024
Created: 2026-09-20
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PERMISSIONS = (
    "tasks",
    "comments",
    "goals",
    "board",
    "files",
    "vault",
    "vault_reveal",
    "people",
    "agents",
    "project",
)
"""Mirrors ``app.auth.permissions.Permission``.

Spelled out rather than imported, for the reason revision 0024 spells out the
palette: a migration describes what it did at the moment it ran, and a
vocabulary that gains an eleventh entry later must not silently change what
this back-fill granted.
"""


def upgrade() -> None:
    op.create_table(
        "project_permission",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("permission", sa.String(length=32), nullable=False),
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
            name=op.f("fk_project_permission_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_project_permission_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_permission")),
    )
    op.create_index(
        "ix_project_permission_role_id_permission",
        "project_permission",
        ["role_id", "permission"],
        unique=True,
        postgresql_where=sa.text("role_id IS NOT NULL"),
    )
    op.create_index(
        "ix_project_permission_project_id_permission",
        "project_permission",
        ["project_id", "permission"],
        unique=True,
        postgresql_where=sa.text("role_id IS NULL"),
    )

    _open_every_project()


def _open_every_project() -> None:
    """Grant everything to every role and to every project's baseline.

    Two statements, one for each kind of row, because they are two different
    facts and somebody reading this later will want to see which one gave
    their board what. ``uuidv7()`` is not available on every Postgres this
    runs against, so ids come from ``gen_random_uuid()`` — a permission grant
    is never ordered by its id, and pgcrypto's function has been in core since
    13.
    """
    connection = op.get_bind()

    connection.execute(
        sa.text(
            """
            INSERT INTO project_permission (id, project_id, role_id, permission)
            SELECT gen_random_uuid(), p.id, NULL, permission
              FROM project AS p
              CROSS JOIN unnest(CAST(:permissions AS text[])) AS permission
            """
        ),
        {"permissions": list(_PERMISSIONS)},
    )

    connection.execute(
        sa.text(
            """
            INSERT INTO project_permission (id, project_id, role_id, permission)
            SELECT gen_random_uuid(), r.project_id, r.id, permission
              FROM project_role AS r
              CROSS JOIN unnest(CAST(:permissions AS text[])) AS permission
             WHERE NOT r.is_admin
            """
        ),
        {"permissions": list(_PERMISSIONS)},
    )


def downgrade() -> None:
    op.drop_index("ix_project_permission_project_id_permission", table_name="project_permission")
    op.drop_index("ix_project_permission_role_id_permission", table_name="project_permission")
    op.drop_table("project_permission")
