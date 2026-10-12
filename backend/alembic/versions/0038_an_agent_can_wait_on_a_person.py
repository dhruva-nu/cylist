"""An agent can wait on a person: a question it asked, or a permission.

``agent_prompt`` is one thing an agent session is waiting on a person for —
an ``ask_human`` question (CYLIST-85) or a Claude Code permission request
(CYLIST-89) — with the choices offered, and once closed, what was answered,
by whom and when. Each belongs to its session and that session's card, and
both cascade. ``kind`` and ``status`` are VARCHARs with a CHECK, never native
enums, for the reason ``models/task.py`` gives; two more CHECKs tie
``closed_at`` to ``status`` and ``answered_at`` to ``answered``.

Pure ``CREATE TABLE``: nothing existing is touched, and the downgrade drops it.

Revision ID: 0038
Revises: 0037
Created: 2026-10-12
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0038"
down_revision: str | None = "0037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "agent_prompt",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("agent_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("tool", sa.String(length=200), nullable=True),
        sa.Column("options", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=9), nullable=False),
        sa.Column("answer", sa.Text(), nullable=True),
        sa.Column("answer_key", sa.String(length=40), nullable=True),
        sa.Column("answered_by_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("answered_by_label", sa.String(length=120), nullable=True),
        sa.Column("closed_reason", sa.String(length=40), nullable=True),
        sa.Column("asked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_polled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'open') = (closed_at IS NULL)",
            name=op.f("ck_agent_prompt_open_has_no_closed_at"),
        ),
        sa.CheckConstraint(
            "(status = 'answered') = (answered_at IS NOT NULL)",
            name=op.f("ck_agent_prompt_answered_has_answered_at"),
        ),
        sa.CheckConstraint(
            "kind IN ('question', 'permission')",
            name=op.f("ck_agent_prompt_agent_prompt_kind"),
        ),
        sa.CheckConstraint(
            "status IN ('open', 'answered', 'expired', 'withdrawn')",
            name=op.f("ck_agent_prompt_agent_prompt_status"),
        ),
        sa.ForeignKeyConstraint(
            ["agent_session_id"],
            ["agent_session.id"],
            name=op.f("fk_agent_prompt_agent_session_id_agent_session"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["answered_by_id"],
            ["person.id"],
            name=op.f("fk_agent_prompt_answered_by_id_person"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["task_id"],
            ["task.id"],
            name=op.f("fk_agent_prompt_task_id_task"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_prompt")),
    )
    op.create_index("ix_agent_prompt_agent_session_id", "agent_prompt", ["agent_session_id"])
    op.create_index("ix_agent_prompt_task_id", "agent_prompt", ["task_id"])
    op.create_index(
        "ix_agent_prompt_agent_session_id_open",
        "agent_prompt",
        ["agent_session_id"],
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_prompt_agent_session_id_open", table_name="agent_prompt")
    op.drop_index("ix_agent_prompt_task_id", table_name="agent_prompt")
    op.drop_index("ix_agent_prompt_agent_session_id", table_name="agent_prompt")
    op.drop_table("agent_prompt")
