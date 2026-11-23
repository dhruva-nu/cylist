"""Passkeys, and recovery that does not need a password (CYLIST-204).

``person_credential`` holds each person's passkeys (public key only).
``passkey_challenge`` and ``recovery_request`` hold the two ceremonies that
are in flight. ``person.password_locked`` is the operator's way of switching a
compromised password off without deleting the account.

Revision ID: 0055
Revises: 0054
Created: 2026-11-23
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0055"
down_revision: str | None = "0054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "person",
        sa.Column("password_locked", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    op.create_table(
        "person_credential",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("credential_id", sa.LargeBinary(), nullable=False),
        sa.Column("public_key", sa.LargeBinary(), nullable=False),
        sa.Column("sign_count", sa.Integer(), nullable=False),
        sa.Column("transports", postgresql.ARRAY(sa.Text()), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name="fk_person_credential_person_id_person",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_person_credential"),
    )
    op.create_index(
        "ix_person_credential_credential_id", "person_credential", ["credential_id"], unique=True
    )
    op.create_index("ix_person_credential_person_id", "person_credential", ["person_id"])

    op.create_table(
        "passkey_challenge",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "purpose",
            sa.Enum(
                "register",
                "login",
                name="challenge_purpose",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("challenge", sa.LargeBinary(), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name="fk_passkey_challenge_person_id_person",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_passkey_challenge"),
    )
    op.create_index("ix_passkey_challenge_expires_at", "passkey_challenge", ["expires_at"])

    op.create_table(
        "recovery_request",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "origin",
            sa.Enum(
                "device",
                "operator",
                name="recovery_origin",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("secret_hash", sa.String(length=64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by_token_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["person_id"],
            ["person.id"],
            name="fk_recovery_request_person_id_person",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_recovery_request"),
    )
    op.create_index("ix_recovery_request_code_hash", "recovery_request", ["code_hash"], unique=True)
    op.create_index("ix_recovery_request_expires_at", "recovery_request", ["expires_at"])


def downgrade() -> None:
    op.drop_table("recovery_request")
    op.drop_table("passkey_challenge")
    op.drop_table("person_credential")
    op.drop_column("person", "password_locked")
