"""Somewhere for old activity to go, and an index for finding old sessions.

CYLIST-139. ``activity`` and ``agent_session`` (with its feed and prompts)
only ever grew. Retention now trims them once an hour — see
``app/services/retention.py`` — and this is what it needs from the schema.

``activity_archive`` is where trail rows past the retention window are moved:
the same columns, the hot row's own id, and when it was moved. No foreign keys
and no index but its key, because nothing reads it in the ordinary course and
the record has to outlive whatever it names.

``ix_agent_session_dismissed_at`` is partial, over the dismissed rows only,
which are the only rows the pass may take. The activity side needs nothing
new: ``ix_activity_occurred_at`` already answers "oldest first".

The downgrade puts archived rows back on the trail rather than dropping them
— this is the audit record, and a downgrade is not a reason to lose it. A
reference to a token, person or project that has since gone is nulled on the
way, as the hot table's own foreign keys would have done.

Revision ID: 0047
Revises: 0046
Created: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0047"
down_revision: str | None = "0046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "activity_archive",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("actor_label", sa.String(length=120), nullable=False),
        sa.Column("channel", sa.String(length=16), nullable=False),
        sa.Column("verb", sa.String(length=64), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "archived_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_activity_archive"),
    )
    op.create_index(
        "ix_agent_session_dismissed_at",
        "agent_session",
        ["dismissed_at"],
        postgresql_where=sa.text("dismissed_at IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_session_dismissed_at", table_name="agent_session")
    op.execute(
        """
        INSERT INTO activity (
            id, occurred_at, actor_token_id, actor_person_id, actor_label,
            channel, verb, entity_type, entity_id, project_id, payload
        )
        SELECT a.id, a.occurred_at, t.id, p.id, a.actor_label,
               a.channel, a.verb, a.entity_type, a.entity_id, pr.id, a.payload
        FROM activity_archive AS a
        LEFT JOIN api_token AS t ON t.id = a.actor_token_id
        LEFT JOIN person AS p ON p.id = a.actor_person_id
        LEFT JOIN project AS pr ON pr.id = a.project_id
        ON CONFLICT (id) DO NOTHING
        """
    )
    op.drop_table("activity_archive")
