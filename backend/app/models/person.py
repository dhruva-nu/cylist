"""People: the team and the clients, and which of them can sign in.

The directory is global rather than per-project. A client who appears on three
projects is one row, so their details are edited once and a task can name them
whoever is looking.

A directory entry is also where an account lives. Giving a team member a
password turns their row into someone who can sign in as themselves; a row
with no ``password_hash`` is a name on a card and nothing more, which is what
every client and every teammate who has not been invited yet stays. There is
no separate ``user`` table, because there is no such thing as a user who is
not in the directory: the person the board assigns work to and the person who
signs in to do it are the same person, and two tables would only be two places
to keep that agreement.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Case, DateTime, Enum, Index, String, Text, case, text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

_IS_ACCOUNT = "password_hash IS NOT NULL OR invite_token_hash IS NOT NULL"
"""The rows an email address has to be unique across.

Signing in is "find the person with this email", so two people who can sign in
cannot share one. Two who cannot are free to: a shared ``support@`` address
against two client contacts is a real thing a directory holds, and refusing it
would be inventing a rule to protect a lookup that never happens for them.

Written once here because the index and the service that checks before writing
have to mean the same thing by "account", and a predicate spelled out twice is
a predicate that eventually says two things.
"""


class PersonKind(StrEnum):
    """Which side of the work someone is on."""

    TEAM = "team"
    """Does the work."""

    CLIENT = "client"
    """Commissions, approves or unblocks the work."""


class Person(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "person"
    __table_args__ = (
        Index("ix_person_kind_archived_at", "kind", "archived_at"),
        # Case-insensitive, because nobody who typed their address with a
        # capital in it thinks they have a different one, and because the
        # login lookup folds case for exactly that reason.
        Index(
            "ix_person_account_email",
            text("lower(email)"),
            unique=True,
            postgresql_where=text(_IS_ACCOUNT),
        ),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)

    kind: Mapped[PersonKind] = mapped_column(
        Enum(
            PersonKind,
            name="person_kind",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(String(160), nullable=False)
    """Who this is, in one line — "Finance controller, Atlas"."""

    responsibilities: Mapped[str] = mapped_column(Text, nullable=False)
    """What they do, and so what you would tag them about when work stalls."""

    email: Mapped[str | None] = mapped_column(String(254))
    """Also the username. Unique among the rows that can sign in — see
    :data:`_IS_ACCOUNT`."""

    colour: Mapped[str] = mapped_column(String(7), nullable=False)

    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """People are archived, never deleted: tasks and files keep pointing at
    whoever created them long after they leave the project.

    Archiving is also how access is withdrawn. It does not clear the password
    — bringing somebody back should not mean setting one again — so nothing
    but :attr:`is_archived` stands between an archived account and a valid
    credential, and both the login path and every request check it.
    """

    password_hash: Mapped[str | None] = mapped_column(String(255))
    """Argon2id hash of this person's own password, or ``None``.

    ``None`` is the ordinary state: clients never get one, and a teammate has
    one only once they have accepted an invitation. Nullable rather than empty
    so "has no account" is a fact SQL can be asked about, and so the column
    can never be mistaken for a hash of the empty string.
    """

    invite_token_hash: Mapped[str | None] = mapped_column(String(64))
    """SHA-256 of the outstanding invitation's one-time token, if there is one.

    Hashed for the same reason an API token is: the database is not where the
    thing that grants access should be readable. Cleared the moment the
    invitation is accepted, so an accepted link cannot be replayed.
    """

    invite_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When that invitation stops working. An invite that never expires is a
    password sitting in somebody's mailbox forever."""

    @property
    def is_archived(self) -> bool:
        return self.archived_at is not None

    @property
    def has_account(self) -> bool:
        """Whether this person can sign in — today, not in principle.

        False for an archived account, because the answer callers want from
        this is "can they get in", and an archived person cannot.
        """
        return self.password_hash is not None and not self.is_archived

    @property
    def invite_is_pending(self) -> bool:
        """Whether an unexpired, unaccepted invitation is outstanding."""
        return (
            self.invite_token_hash is not None
            and self.invite_expires_at is not None
            and self.invite_expires_at > now()
            and not self.is_archived
        )


KIND_ORDER: Case[int] = case(
    (Person.kind == PersonKind.TEAM, 0),
    (Person.kind == PersonKind.CLIENT, 1),
    else_=2,
)
"""Sort key putting the team before clients.

Ordering by the column itself would be alphabetical — "client" before "team" —
which reads backwards on a page whose first heading is Team.
"""
