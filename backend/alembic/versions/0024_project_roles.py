"""Everyone on a project is something on it, and somebody says what.

CYLIST-44 gave everybody their own account; this gives a project somewhere to
record who those people are on it. A role is a name a project's admin invents
— "Reviewer", "QA" — and hands out. What a role *permits* is deliberately not
part of this: the only authority any of them carries is the admin role's, and
the only thing it lets its holder do is manage the others.

**What arrives.** ``project_role``, and ``project_member.role_id`` pointing at
it. Every project is given one role, ``Admin``.

**What is renamed.** ``person.role`` becomes ``person.title``. It was always
the job title — "Finance controller, Atlas" — and leaving it called role
beside a column that means something else would have left two different things
under one word forever. This is a breaking change on the wire: ``PersonRead``,
``PersonCreate`` and ``PersonUpdate`` all say ``title`` now, as do the CLI's
``people`` table and the MCP server's ``list_people``.

**Who the back-fill makes admin.** The earliest-joined member of each project,
by ``project_member.created_at``. There is no record of who created a project
— ``activity`` rows from before CYLIST-44 carry no actor at all — and the
first person put on a board is the closest honest proxy for the person who
started it. A project with no members gets the role with nobody in it, which
is the same state a project created by the bootstrap session starts in, and
:func:`app.services.roles.claim_admin_if_vacant` resolves it the moment
somebody is added.

**The foreign key is composite and deferred**, and both halves of that are
deliberate. Composite — ``(project_id, role_id)`` against
``(project_id, id)`` — so that "a member's role belongs to the member's
project" is true in the database rather than true by inspection; the unique
constraint on ``project_role`` exists only to give it something to point at.
Deferred, with ``NO ACTION`` rather than ``RESTRICT``, because ``project_role``
and ``project_member`` both cascade from ``project``: an immediate check would
accept or refuse deleting a project depending on which of the two cascades
Postgres fired first. Deferred, both sets of rows are gone before anything is
checked, and deleting a role somebody still holds is still refused — at commit
time, and by :func:`app.services.roles.delete` well before that, which can
name who is wearing it.

**The downgrade loses every role**, the admin one included, and puts the job
title back under its old name. Nothing else is touched: membership, people and
boards come through unchanged.

Revision ID: 0024
Revises: 0023
Created: 2026-09-19
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.core.ids import uuid7

revision: str = "0024"
down_revision: str | None = "0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ADMIN_NAME = "Admin"
"""Mirrors ``app.models.role.ADMIN_ROLE_NAME``."""

_ADMIN_DESCRIPTION = "Manages this project's roles and who holds them."
"""Mirrors the description ``app.services.roles.seed_admin`` writes."""

_PALETTE = (
    "#1D7D46",
    "#3B6FC2",
    "#A76900",
    "#7A6B9E",
    "#8E6A3D",
    "#278655",
    "#B5533F",
    "#4A7B8C",
)
"""Mirrors ``app.core.palette.PALETTE``.

Spelled out rather than imported because a migration describes the database as
it was at the moment it ran, and a palette that gained a ninth colour later
would silently change what this revision did.
"""


def _colour_for(key: str) -> str:
    """The same stable palette pick :func:`app.core.palette.colour_for` makes."""
    seed = f"{key}:{_ADMIN_NAME}".strip().casefold().encode()
    digest = hashlib.blake2b(seed, digest_size=8).digest()
    return _PALETTE[int.from_bytes(digest) % len(_PALETTE)]


def upgrade() -> None:
    op.create_table(
        "project_role",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("colour", sa.String(length=7), nullable=False),
        sa.Column("is_admin", sa.Boolean(), server_default=sa.text("false"), nullable=False),
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
            name=op.f("fk_project_role_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_project_role")),
        sa.UniqueConstraint("project_id", "id", name="uq_project_role_project_id_id"),
    )
    op.create_index(
        "ix_project_role_project_id_name",
        "project_role",
        ["project_id", sa.text("lower(name)")],
        unique=True,
    )
    op.create_index(
        "ix_project_role_project_id_admin",
        "project_role",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("is_admin"),
    )

    op.add_column(
        "project_member",
        sa.Column("role_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_project_member_project_id_role_id_project_role",
        "project_member",
        "project_role",
        ["project_id", "role_id"],
        ["project_id", "id"],
        ondelete="NO ACTION",
        deferrable=True,
        initially="DEFERRED",
    )

    op.alter_column("person", "role", new_column_name="title")

    _give_every_project_an_admin()


def _give_every_project_an_admin() -> None:
    """Seed the Admin role on every existing project and find it a holder.

    One statement per step rather than one clever one, because the second step
    depends on ids the first hands out and because "who ended up admin" is a
    question somebody will ask of this migration later.

    The holder is chosen with ``DISTINCT ON`` over the membership rows: the
    earliest to join each project, tie-broken by when the person themselves was
    added so that a board whose whole membership was written in one transaction
    still resolves to one row rather than an arbitrary one. Archived people are
    skipped — they cannot sign in, so making one the admin would leave the
    project administered by nobody.
    """
    connection = op.get_bind()

    projects = connection.execute(sa.text("SELECT id, key FROM project")).all()
    for project_id, key in projects:
        connection.execute(
            sa.text(
                """
                INSERT INTO project_role
                    (id, project_id, name, description, colour, is_admin)
                VALUES
                    (:id, :project_id, :name, :description, :colour, true)
                """
            ),
            {
                "id": uuid7(),
                "project_id": project_id,
                "name": _ADMIN_NAME,
                "description": _ADMIN_DESCRIPTION,
                "colour": _colour_for(key),
            },
        )

    connection.execute(
        sa.text(
            """
            UPDATE project_member AS m
               SET role_id = r.id
              FROM project_role AS r
             WHERE r.project_id = m.project_id
               AND r.is_admin
               AND (m.project_id, m.person_id) IN (
                     SELECT DISTINCT ON (pm.project_id) pm.project_id, pm.person_id
                       FROM project_member AS pm
                       JOIN person AS p ON p.id = pm.person_id
                      WHERE p.archived_at IS NULL
                      ORDER BY pm.project_id, pm.created_at, p.created_at, p.name
                   )
            """
        )
    )


def downgrade() -> None:
    op.alter_column("person", "title", new_column_name="role")

    op.drop_constraint(
        "fk_project_member_project_id_role_id_project_role",
        "project_member",
        type_="foreignkey",
    )
    op.drop_column("project_member", "role_id")

    op.drop_index("ix_project_role_project_id_admin", table_name="project_role")
    op.drop_index("ix_project_role_project_id_name", table_name="project_role")
    op.drop_table("project_role")
