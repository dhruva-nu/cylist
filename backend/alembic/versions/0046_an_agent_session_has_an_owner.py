"""An agent session has an owner, and a socket that says when it last vouched.

Two columns on ``agent_session``.

``person_id`` (CYLIST-120) is whose session it is. Rows were keyed by the
client's own conversation id alone, and that id was readable by anyone who
could read the card — so anyone with a write token could report as, end, or
take over the socket of another person's session. Every existing row is given
the person behind the token that wrote it. A row whose token has since been
deleted cannot be traced to anyone and is left null: it is owned by nobody,
and so reachable by nobody, which is the right answer for history. The unique
key becomes (task, person, client session), so that nobody can claim
another's conversation id on a card first and lock them out of it.

The id is also scrubbed from the activity payloads that carried it. It was
the one thing a reader needed to impersonate a session; the owner already
knows it, and nothing reads it back. The downgrade cannot put it back, and
does not need to — nothing before this revision read it either.

``socket_seen_at`` (CYLIST-133) is when a socket last vouched for the session.
Whether a session is witnessed was a question about one process's memory, and
no second process could answer it. It starts null everywhere: an open row
that no socket has stamped yet is treated like one reported over HTTP, and
its daemon stamps it within seconds of reconnecting.

Revision ID: 0046
Revises: 0045
Created: 2026-10-21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "agent_session",
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "agent_session",
        sa.Column("socket_seen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_agent_session_person_id_person",
        "agent_session",
        "person",
        ["person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.execute(
        """
        UPDATE agent_session AS s
        SET person_id = t.person_id
        FROM api_token AS t
        WHERE t.id = s.token_id
        """
    )

    op.drop_index("ix_agent_session_task_id_client_session_id", table_name="agent_session")
    op.create_index(
        "ix_agent_session_task_id_person_id_client_session_id",
        "agent_session",
        ["task_id", "person_id", "client_session_id"],
        unique=True,
    )

    op.execute(
        """
        UPDATE activity
        SET payload = payload - 'client_session_id'
        WHERE verb LIKE 'agent\\_session.%' AND payload ? 'client_session_id'
        """
    )


def downgrade() -> None:
    # Two people's rows may now share a (task, client session) pair, which the
    # old key forbids. The newest of each pair is the one kept, as the one a
    # card would show.
    op.execute(
        """
        DELETE FROM agent_session AS s
        USING agent_session AS newer
        WHERE newer.task_id = s.task_id
          AND newer.client_session_id = s.client_session_id
          AND newer.id > s.id
        """
    )
    op.drop_index(
        "ix_agent_session_task_id_person_id_client_session_id", table_name="agent_session"
    )
    op.create_index(
        "ix_agent_session_task_id_client_session_id",
        "agent_session",
        ["task_id", "client_session_id"],
        unique=True,
    )
    op.drop_constraint("fk_agent_session_person_id_person", "agent_session", type_="foreignkey")
    op.drop_column("agent_session", "socket_seen_at")
    op.drop_column("agent_session", "person_id")
