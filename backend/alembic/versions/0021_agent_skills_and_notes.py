"""Skills an agent can be given, and the scratchpad it writes on.

Two new tables and nothing back-filled — there is no past to fill them from.

``skill`` points at ``blob`` with ``ON DELETE RESTRICT``, the same way
``file_item`` does: the bytes are shared between the two, so a skill uploaded
from a file already in the project costs one copy on disk, and neither table
may take the content out from under the other.

``agent_note`` carries its length cap as a check constraint rather than only in
the column type, because the cap is the feature: the scratchpad is for one line
of what was learned, and a column that merely truncates at 280 would store a
sentence cut off mid-word.

Revision ID: 0021
Revises: 0020
Created: 2026-09-08
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NOTE_MAX_LENGTH = 280


def upgrade() -> None:
    op.create_table(
        "skill",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("blob_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("size", sa.BigInteger(), nullable=False),
        sa.Column("mime", sa.String(length=255), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_skill_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["blob_id"],
            ["blob.id"],
            name=op.f("fk_skill_blob_id_blob"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["person.id"],
            name=op.f("fk_skill_added_by_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_skill")),
    )
    op.create_index("ix_skill_project_id_name", "skill", ["project_id", "name"], unique=True)
    op.create_index("ix_skill_blob_id", "skill", ["blob_id"], unique=False)

    op.create_table(
        "agent_note",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.String(length=_NOTE_MAX_LENGTH), nullable=False),
        sa.Column("author_label", sa.String(length=120), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            f"char_length(btrim(body)) BETWEEN 1 AND {_NOTE_MAX_LENGTH}",
            name=op.f("ck_agent_note_body_is_short_and_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_agent_note_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["person.id"],
            name=op.f("fk_agent_note_added_by_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_note")),
    )
    op.create_index(
        "ix_agent_note_project_id_created_at",
        "agent_note",
        ["project_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_agent_note_project_id_created_at", table_name="agent_note")
    op.drop_table("agent_note")
    op.drop_index("ix_skill_blob_id", table_name="skill")
    op.drop_index("ix_skill_project_id_name", table_name="skill")
    op.drop_table("skill")
