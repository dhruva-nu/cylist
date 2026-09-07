"""Task priority said as a level: p0, p1, p2, p3.

The scale is the one it always was — 0 is drop everything, 3 is the default —
but "asap" and "week" were one team's words for it, and a card marked ASAP had
to be translated by everybody who read it. Every existing row moves to the
level it already meant, so no card changes urgency here, only wording.

Revision ID: 0016
Revises: 0015
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

_LEVELS = ("p0", "p1", "p2", "p3")

_RENAMED = {"urgent": "p0", "asap": "p1", "week": "p2", "someday": "p3"}

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _relabel(mapping: dict[str, str]) -> None:
    for was, now in mapping.items():
        op.execute(
            sa.text("UPDATE task SET priority = :now WHERE priority = :was").bindparams(
                now=now, was=was
            )
        )


def _retype(levels: tuple[str, ...]) -> None:
    """Narrow or widen the column to fit exactly the values it now holds.

    The type is ``VARCHAR(n)`` under a non-native enum, so its width follows
    the longest member — which is why the two directions below order the type
    change and the rewrite differently: a column can be widened before its rows
    change, and can only be narrowed after they have.
    """
    op.alter_column(
        "task",
        "priority",
        type_=sa.Enum(*levels, name="task_priority", native_enum=False),
        existing_nullable=False,
    )


def upgrade() -> None:
    # The default first: a column cannot be narrowed while a default it no
    # longer permits is still attached to it.
    op.execute("ALTER TABLE task ALTER COLUMN priority DROP DEFAULT")
    _relabel(_RENAMED)
    _retype(_LEVELS)
    op.execute("ALTER TABLE task ALTER COLUMN priority SET DEFAULT 'p3'")


def downgrade() -> None:
    op.execute("ALTER TABLE task ALTER COLUMN priority DROP DEFAULT")
    _retype(("urgent", "asap", "week", "someday"))
    _relabel({now: was for was, now in _RENAMED.items()})
    op.execute("ALTER TABLE task ALTER COLUMN priority SET DEFAULT 'someday'")
