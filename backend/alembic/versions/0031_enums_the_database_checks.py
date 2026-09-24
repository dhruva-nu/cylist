"""Every enum column is checked by the database, not only by the ORM.

Each enum is stored as a ``VARCHAR`` of its values rather than a native
Postgres type, so that a member can be added inside a transaction. The comment
on ``api_token.kind`` calls that "a VARCHAR + CHECK" — but SQLAlchemy only
writes the CHECK when asked, and it never was. So the database took any
string that fitted the width, and the first thing to notice a
bad one was the ORM, on the next *read*: a card whose status is not a
:class:`~app.models.task.TaskStatus` does not fail where it was written, it
fails every board it is on. A hand-run ``UPDATE``, a downgrade that forgot to
rewrite a value, a future migration's typo — each would have been found by
whoever next opened the page.

This adds the CHECK to fourteen columns, named ``ck_<table>_<enum>`` as the
models now declare them with ``create_constraint=True``, and listing exactly
the members each enum has today. ``vault_secret.node_kind`` is left alone:
``ck_vault_secret_node_kind_is_secret`` already pins it to one value.

**A value outside the list stops the upgrade.** It is read first, and if any
row holds one the migration raises, naming the table, the column, the value
and how many rows — and, because Alembic runs the whole upgrade in one
transaction, nothing from this or the revisions before it is applied. It does
not guess a replacement: there is no member such a row obviously meant, and
since the ORM cannot load it, the application has not been able to show that
row since it was written either. The deploy script runs migrations before it
replaces the running app, so a refusal leaves the old version serving.

The cost is the one the models' ``_enum`` helpers now spell out: a new member
is a migration that swaps the CHECK, where before it was a migration only when
the member was the longest yet (0022). One rule instead of a rule that depends
on a string's length.

Revision ID: 0031
Revises: 0030
Created: 2026-09-25
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ENUMS: tuple[tuple[str, str, str, tuple[str, ...]], ...] = (
    ("activity", "channel", "channel", ("web", "api")),
    ("api_token", "kind", "token_kind", ("session", "api")),
    ("person", "kind", "person_kind", ("team", "client")),
    ("goal", "status", "goal_status", ("open", "achieved", "dropped")),
    ("task", "type", "task_type", ("feature", "bug", "chore")),
    ("task", "priority", "task_priority", ("p0", "p1", "p2", "p3")),
    ("task", "status", "task_status", ("active", "hold", "blocked", "cancelled")),
    ("task_comment", "kind", "comment_kind", ("comment", "status_change")),
    ("task_checklist_item", "state", "checklist_state", ("open", "done", "cancelled")),
    ("file_item", "kind", "item_kind", ("file", "link")),
    ("file_item", "source", "item_source", ("upload", "sharepoint", "gdrive", "other")),
    ("vault_node", "kind", "vault_node_kind", ("branch", "secret")),
    ("agent_session", "state", "agent_session_state", ("working", "waiting", "done")),
    (
        "agent_session",
        "reason",
        "agent_session_reason",
        (
            "turn_ended",
            "permission",
            "idle",
            "question",
            "moved",
            "session_ended",
            "connection_lost",
        ),
    ),
)
"""``(table, column, enum name, members)``, the members as of this revision.

Written out rather than read off the models, for the reason 0024 writes its
palette out: a migration says what the database was when it ran, and an enum
that gains a member later must not change what this one did."""


def _in(column: str, members: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(member) for member in members)})"


def _strays(table: str, column: str, members: tuple[str, ...]) -> list[tuple[str, int]]:
    """The values in a column that are not among its members, with how many."""
    found = op.get_bind().execute(
        sa.text(
            f"SELECT {column}, count(*) FROM {table}"  # noqa: S608 - names from _ENUMS
            f" WHERE {column} IS NOT NULL AND NOT ({_in(column, members)})"
            f" GROUP BY {column} ORDER BY {column}"
        )
    )
    return [(value, count) for value, count in found]


def upgrade() -> None:
    strays = [
        f"{table}.{column} = {value!r} on {count} row{'s' if count != 1 else ''}"
        for table, column, _, members in _ENUMS
        for value, count in _strays(table, column, members)
    ]
    if strays:
        raise RuntimeError(
            "Refusing to add the enum CHECK constraints: these rows hold a value "
            "that is not a member, which the application cannot read either. "
            "Correct them by hand, then run the upgrade again. " + "; ".join(strays)
        )

    for table, column, name, members in _ENUMS:
        op.create_check_constraint(name, table, _in(column, members))


def downgrade() -> None:
    for table, _, name, _ in reversed(_ENUMS):
        op.drop_constraint(name, table, type_="check")
