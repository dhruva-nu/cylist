"""A session keeps a feed of what it read, edited, ran and said.

``agent_feed_entry`` is one line of that feed: written by an agent's presence
daemon as it follows the session's transcript, read by the task dialog. Each
line belongs to the session that wrote it and to that session's card — the
card copied onto the row so that a card's feed is one index scan — and both
cascade, so a deleted card or session takes its feed with it. ``seq`` is an
identity column and the feed's order: a UUIDv7 id orders only across
milliseconds, and one frame's lines are written inside the same one.

``kind`` is a VARCHAR with a CHECK, never a native enum, for the reason
``models/task.py`` gives. The service trims each session to its newest lines
on every write, so the table stays a window rather than a log.

Pure ``CREATE TABLE``: nothing existing is touched, and the downgrade drops it.

Revision ID: 0037
Revises: 0036
Created: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_feed_entry",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.BigInteger(), sa.Identity(always=False), nullable=False),
        sa.Column("agent_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("kind", sa.String(length=6), nullable=False),
        sa.Column("tool", sa.String(length=200), nullable=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "kind IN ('said', 'read', 'edit', 'run', 'tool', 'output', 'error')",
            name=op.f("ck_agent_feed_entry_agent_feed_kind"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_session_id"],
            ["agent_session.id"],
            name=op.f("fk_agent_feed_entry_agent_session_id_agent_session"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name=op.f("fk_agent_feed_entry_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_feed_entry")),
        sa.UniqueConstraint("seq", name=op.f("uq_agent_feed_entry_seq")),
    )
    op.create_index(
        "ix_agent_feed_entry_agent_session_id", "agent_feed_entry", ["agent_session_id"]
    )
    op.create_index("ix_agent_feed_entry_task_id_seq", "agent_feed_entry", ["task_id", "seq"])


def downgrade() -> None:
    op.drop_index("ix_agent_feed_entry_task_id_seq", table_name="agent_feed_entry")
    op.drop_index("ix_agent_feed_entry_agent_session_id", table_name="agent_feed_entry")
    op.drop_table("agent_feed_entry")
