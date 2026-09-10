"""A reason for the sessions nobody said goodbye for.

``AgentSessionReason`` gains ``connection_lost``: the agent's socket went away
without a ``bye``. It covers both halves of the same fact — the connection was
dropped, or it went quiet past the idle window — because the board cannot show
the difference between them and should not pretend to. Which of the two it was
is written to the activity payload as ``cause``, where it costs no schema.

``idle`` is deliberately not reused. It is defined as a reason a session is
*waiting* — the harness sitting on a prompt — and a session that has ended is
not waiting for anything.

Nothing is back-filled. Rows that ended before this revision ended under the
reason they were given, and a reason is a record of what happened rather than
a classification to be restated.

The column has to grow. ``reason`` is a non-native enum, which SQLAlchemy
renders as a ``VARCHAR`` sized to the longest member it knew at the time:
``session_ended`` is 13 characters, and ``connection_lost`` is 15. Without the
widen the first row written would fail on ``StringDataRightTruncation`` rather
than on anything that names the cause.

There is no check constraint to rewrite. ``app.models.agent_session._enum``
leaves ``create_constraint`` at its default of ``False``, so the enum is a
``VARCHAR`` and nothing else — the values are enforced by the application, not
by the database.

Revision ID: 0022
Revises: 0021
Created: 2026-09-10
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_WAS = 13
"""``session_ended``, the longest reason before this revision."""

_NOW = 15
"""``connection_lost``."""


def upgrade() -> None:
    op.alter_column(
        "agent_session",
        "reason",
        existing_type=sa.VARCHAR(length=_WAS),
        type_=sa.VARCHAR(length=_NOW),
        existing_nullable=True,
    )


def downgrade() -> None:
    # The rows that carry the new reason have to go back to one that fits, and
    # `session_ended` is what they would have been written as before it
    # existed: the session is over either way, and only the cause is lost.
    op.execute("UPDATE agent_session SET reason = 'session_ended' WHERE reason = 'connection_lost'")
    op.alter_column(
        "agent_session",
        "reason",
        existing_type=sa.VARCHAR(length=_NOW),
        type_=sa.VARCHAR(length=_WAS),
        existing_nullable=True,
    )
