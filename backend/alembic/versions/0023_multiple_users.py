"""More than one person can sign in.

Cylist was built for one owner: one password in ``CYLIST_PASSWORD_HASH``, and
one ``person.is_me`` row that the partial unique index guaranteed there was
never two of. This revision replaces both with accounts on the directory
itself.

**What arrives.** ``person`` gains ``password_hash``, ``invite_token_hash``
and ``invite_expires_at``; ``api_token`` gains the ``person_id`` it acts as;
``activity`` gains the ``actor_person_id`` behind the credential.

**What leaves.** ``person.is_me`` and its index. "Who is you" is no longer a
property of a row — with several people signed in it is a property of the
request — so keeping the column would mean keeping a second, contradictory
answer to a question the session already answers.

**The owner is carried across, not asked to start again.** The existing
``is_me`` person becomes the first account: their ``password_hash`` is set to
whatever is in ``CYLIST_PASSWORD_HASH``, so the password that worked
yesterday still works, typed alongside their email instead of alone. Their
tokens and their activity rows are back-filled to point at them, because they
were the only person who could have created any of them.

Two ways that can come out differently, both deliberate:

* **No ``is_me`` row, or no configured hash.** Nothing is back-filled, and
  the deployment comes up with no accounts — which is exactly the state the
  bootstrap login in ``app.routers.auth`` exists for. Someone signs in with
  ``CYLIST_PASSWORD_HASH`` and opens the first real account from there.
* **The ``is_me`` person has no email.** They still get the password, but
  they cannot sign in with it until somebody gives them an address, because
  the address is the username. The upgrade does not invent one; it leaves a
  row that ``GET /people`` will show as an account with nothing to log in
  with, which is visible and fixable, unlike a guess.

The setting is read here rather than passed in because it is already the
process's own configuration — the app this migration runs beside boots from
the same ``.env``. A migration that reached for a *secret it did not already
have* would be a different matter; this one is moving a value from one place
the deployment already keeps it to another.

The new email index is partial and case-folded: unique across the rows that
can sign in, and nowhere else. Two client contacts sharing a ``support@``
address is a real thing a directory holds and not something this revision has
any business breaking. If the upgrade fails on it, two people who can sign in
share an address — which was never possible before, so in practice this only
fires on data that arrived some other way.

Revision ID: 0023
Revises: 0022
Created: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ACCOUNT = "password_hash IS NOT NULL OR invite_token_hash IS NOT NULL"
"""Mirrors ``app.models.person._IS_ACCOUNT``."""


def upgrade() -> None:
    op.add_column("person", sa.Column("password_hash", sa.String(length=255), nullable=True))
    op.add_column("person", sa.Column("invite_token_hash", sa.String(length=64), nullable=True))
    op.add_column(
        "person",
        sa.Column("invite_expires_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.add_column("api_token", sa.Column("person_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.create_foreign_key(
        "fk_api_token_person_id_person",
        "api_token",
        "person",
        ["person_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.create_index("ix_api_token_person_id", "api_token", ["person_id"])

    op.add_column(
        "activity", sa.Column("actor_person_id", postgresql.UUID(as_uuid=True), nullable=True)
    )
    op.create_foreign_key(
        "fk_activity_actor_person_id_person",
        "activity",
        "person",
        ["actor_person_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_activity_actor_person_id_occurred_at",
        "activity",
        ["actor_person_id", "occurred_at"],
    )

    _adopt_the_owner()

    op.drop_index("ix_person_is_me", table_name="person")
    op.drop_column("person", "is_me")

    op.create_index(
        "ix_person_account_email",
        "person",
        [sa.text("lower(email)")],
        unique=True,
        postgresql_where=sa.text(_ACCOUNT),
    )


def _adopt_the_owner() -> None:
    """Turn the single ``is_me`` person into the deployment's first account.

    Runs while ``is_me`` is still there — it is the only record of which row
    this is — and before the email index is created, so a deployment whose
    owner shares an address with a client is not refused over a row that is
    about to stop mattering.
    """
    password_hash = get_settings().password_hash
    if not password_hash:
        # Nothing to carry across. The deployment comes up account-less and
        # signs in through the bootstrap path, which is the same place a
        # brand-new one starts.
        return

    connection = op.get_bind()
    owner_id = connection.scalar(sa.text("SELECT id FROM person WHERE is_me LIMIT 1"))
    if owner_id is None:
        return

    connection.execute(
        sa.text("UPDATE person SET password_hash = :hash WHERE id = :id"),
        {"hash": password_hash, "id": owner_id},
    )
    # Every credential and every audit row predates accounts, so all of them
    # are the owner's: there was nobody else who could have made one.
    connection.execute(
        sa.text("UPDATE api_token SET person_id = :id WHERE person_id IS NULL"),
        {"id": owner_id},
    )
    connection.execute(
        sa.text("UPDATE activity SET actor_person_id = :id WHERE actor_person_id IS NULL"),
        {"id": owner_id},
    )


def downgrade() -> None:
    op.drop_index("ix_person_account_email", table_name="person")

    op.add_column(
        "person",
        sa.Column("is_me", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )
    # Whoever held the first account is the closest thing to the owner there
    # was; oldest first, because that is the one the upgrade would have
    # adopted. Every other account simply stops being able to sign in, which
    # is what going back to a single-owner build means.
    op.execute(
        """
        UPDATE person SET is_me = true WHERE id = (
            SELECT id FROM person
            WHERE password_hash IS NOT NULL AND archived_at IS NULL
            ORDER BY created_at LIMIT 1
        )
        """
    )
    op.create_index(
        "ix_person_is_me",
        "person",
        ["is_me"],
        unique=True,
        postgresql_where=sa.text("is_me"),
    )

    op.drop_index("ix_activity_actor_person_id_occurred_at", table_name="activity")
    op.drop_constraint("fk_activity_actor_person_id_person", "activity", type_="foreignkey")
    op.drop_column("activity", "actor_person_id")

    op.drop_index("ix_api_token_person_id", table_name="api_token")
    op.drop_constraint("fk_api_token_person_id_person", "api_token", type_="foreignkey")
    op.drop_column("api_token", "person_id")

    op.drop_column("person", "invite_expires_at")
    op.drop_column("person", "invite_token_hash")
    op.drop_column("person", "password_hash")
