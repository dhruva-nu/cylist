"""Hooks send a board's changes out, and keep a log of every attempt.

``hook`` is a project's rule for which changes to POST, and where: the verbs it
fires on (none meaning all), optional filters on the card — the column it is
in or came from, its template, its type — a URL, and a signing secret sealed
under the vault key. The filter columns are bare ids rather than foreign keys
on purpose; see ``app/models/hook.py`` for why a deleted column has to make a
filter match nothing rather than be cascaded or nulled away.

``hook_delivery`` is one event on its way to one hook, written in the same
transaction as the change it reports and sent after the commit. Its partial
index is what the courier asks on every pass: which pending rows are due.

Pure ``CREATE TABLE``: nothing existing is touched, and the downgrade drops
both tables, log included.

Revision ID: 0035
Revises: 0034
Created: 2026-10-06
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _timestamps() -> list[sa.Column[object]]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "hook",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column(
            "verbs",
            postgresql.ARRAY(sa.String(length=64)),
            server_default=sa.text("'{}'"),
            nullable=False,
        ),
        sa.Column("to_column_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("from_column_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("task_type", sa.String(length=7), nullable=True),
        sa.Column("url", sa.String(length=2000), nullable=False),
        sa.Column("secret_ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column("key_version", sa.Integer(), nullable=False),
        sa.Column("secret_hint", sa.String(length=8), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            "task_type IN ('feature', 'bug', 'chore')", name=op.f("ck_hook_task_type")
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_hook_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hook")),
    )
    op.create_index("ix_hook_project_id_name", "hook", ["project_id", "name"], unique=True)

    op.create_table(
        "hook_delivery",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("hook_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("activity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("event", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "state", sa.String(length=9), server_default=sa.text("'pending'"), nullable=False
        ),
        sa.Column("attempt_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_status_code", sa.Integer(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "attempts",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'"),
            nullable=False,
        ),
        *_timestamps(),
        sa.CheckConstraint(
            "state IN ('pending', 'delivered', 'failed')",
            name=op.f("ck_hook_delivery_delivery_state"),
        ),
        sa.ForeignKeyConstraint(
            ["hook_id"],
            ["hook.id"],
            name=op.f("fk_hook_delivery_hook_id_hook"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["activity_id"],
            ["activity.id"],
            name=op.f("fk_hook_delivery_activity_id_activity"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_hook_delivery")),
    )
    op.create_index(
        "ix_hook_delivery_hook_id_created_at", "hook_delivery", ["hook_id", "created_at"]
    )
    op.create_index(
        "ix_hook_delivery_next_attempt_at",
        "hook_delivery",
        ["next_attempt_at"],
        postgresql_where=sa.text("state = 'pending'"),
    )


def downgrade() -> None:
    op.drop_table("hook_delivery")
    op.drop_table("hook")
