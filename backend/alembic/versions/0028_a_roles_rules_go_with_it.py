"""A role's grants, rules and clearance go when the role does.

The three tables that say what a role may do — ``project_permission``,
``role_column_rule`` and ``role_clearance`` — each point at their role through
a composite key to ``(project_role.project_id, project_role.id)``. 0025 and
0026 gave that key ``NO ACTION``, deferred, copied from ``project_member``.

**That was right for a member and wrong for a rule.** A member wearing a role
is a reason not to delete it: :func:`app.services.roles.delete` refuses, and
names who. A rule *about* a role is not — it has nothing left to be about. With
``NO ACTION`` the service had to delete every such row itself before the role,
and it only ever remembered the first table. Deleting a role that had a column
rule or a clearance failed at COMMIT with a foreign-key violation: a 500, after
the service had already decided the delete was fine. The key now cascades,
which is also free of the ordering trouble that made ``project_member``
deferred: when a whole project goes, both paths to a rule delete it.

**One unique constraint per table instead of two partial indexes.** Each table
held "one row per role and thing" as a pair — one partial index over the rows
with a role, one over the baseline rows without — because Postgres counts
NULLs as distinct and a plain constraint would have let the baseline repeat.
``NULLS NOT DISTINCT`` (Postgres 15, which ``vault_node`` already relies on)
says the same thing once. The new constraint leads with ``project_id``, which
is how every read of these tables filters: reading a project's permission grid
was a sequential scan, because neither old index started with the project.

No row changes. The composite key already ties a role to one project, so
``(project_id, role_id, x)`` is unique exactly when ``(role_id, x)`` was, and
the old indexes guarantee there is nothing for the new constraint to refuse.

Revision ID: 0028
Revises: 0027
Created: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABLES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("project_permission", ("permission",)),
    ("role_column_rule", ("column_id",)),
    ("role_clearance", ()),
)
"""Each table, and what a role holds one row per — beyond the role itself."""


def _fk(table: str) -> str:
    return f"fk_{table}_project_id_role_id_project_role"


def _uq(table: str, thing: tuple[str, ...]) -> str:
    return "_".join(("uq", table, "project_id", "role_id", *thing))


def _partials(table: str, thing: tuple[str, ...]) -> tuple[tuple[str, list[str], str], ...]:
    """The two partial indexes 0025 and 0026 wrote, by name, columns and predicate."""
    return (
        ("_".join(("ix", table, "role_id", *thing)), ["role_id", *thing], "role_id IS NOT NULL"),
        (
            "_".join(("ix", table, "project_id", *thing)),
            ["project_id", *thing],
            "role_id IS NULL",
        ),
    )


def _rekey(table: str, ondelete: str) -> None:
    op.drop_constraint(_fk(table), table, type_="foreignkey")
    op.create_foreign_key(
        _fk(table),
        table,
        "project_role",
        ["project_id", "role_id"],
        ["project_id", "id"],
        ondelete=ondelete,
        deferrable=True,
        initially="DEFERRED",
    )


def upgrade() -> None:
    for table, thing in _TABLES:
        _rekey(table, "CASCADE")
        # The constraint before the indexes go, so there is no moment in which
        # nothing holds the table to one row per role.
        op.create_unique_constraint(
            _uq(table, thing),
            table,
            ["project_id", "role_id", *thing],
            postgresql_nulls_not_distinct=True,
        )
        for name, _, _ in _partials(table, thing):
            op.drop_index(name, table_name=table)


def downgrade() -> None:
    for table, thing in _TABLES:
        for name, columns, where in _partials(table, thing):
            op.create_index(name, table, columns, unique=True, postgresql_where=sa.text(where))
        op.drop_constraint(_uq(table, thing), table, type_="unique")
        # Back to refusing. Nothing a cascade removed is restored — those rows
        # belonged to roles that are gone — and nothing left can be refused,
        # because every rule still here has its role.
        _rekey(table, "NO ACTION")
