"""Agent sessions: which harness session is on which card, and how it stands.

One new table and nothing back-filled — there is no past to fill it from. A
row is written by a Claude Code lifecycle hook reporting working / waiting /
done for one (card, session) pair; see ``app.models.agent_session``.

``token_id`` is ``ON DELETE SET NULL`` where ``task_id`` cascades: a card that
is deleted takes the record of who was on it, while a token that is deleted
leaves the record standing under the label it was written with.

Revision ID: 0020
Revises: 0019
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

_STATES = ("working", "waiting", "done")
_REASONS = ("turn_ended", "permission", "idle", "question", "moved", "session_ended")

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_session",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(length=120), nullable=False),
        sa.Column("client_session_id", sa.String(length=200), nullable=False),
        sa.Column("client_name", sa.String(length=200), nullable=True),
        sa.Column(
            "state",
            sa.Enum(*_STATES, name="agent_session_state", native_enum=False),
            nullable=False,
        ),
        sa.Column(
            "reason",
            sa.Enum(*_REASONS, name="agent_session_reason", native_enum=False),
            nullable=True,
        ),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state_changed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(state = 'done') = (ended_at IS NOT NULL)",
            name=op.f("ck_agent_session_done_has_ended_at"),
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name=op.f("fk_agent_session_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["token_id"],
            ["api_token.id"],
            name=op.f("fk_agent_session_token_id_api_token"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_session")),
    )
    op.create_index(
        "ix_agent_session_task_id_client_session_id",
        "agent_session",
        ["task_id", "client_session_id"],
        unique=True,
    )
    op.create_index(
        "ix_agent_session_task_id_open",
        "agent_session",
        ["task_id"],
        unique=False,
        postgresql_where=sa.text("ended_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_session_task_id_open", table_name="agent_session")
    op.drop_index("ix_agent_session_task_id_client_session_id", table_name="agent_session")
    op.drop_table("agent_session")
