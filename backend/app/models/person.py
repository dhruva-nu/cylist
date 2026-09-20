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

One entry is not a person at all. ``is_agent`` marks the machine — see
:data:`AGENT_NAME` — and it lives here for the same reason: a card handed to an
agent has to name somebody, and the only thing a board can name is a directory
entry. It has no email and no password, so it never signs in; it is worked
through an API token, which belongs to whoever minted it.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import Boolean, Case, DateTime, Enum, Index, String, Text, case, text
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


AGENT_NAME = "Agent"
"""What the machine in the directory is called.

One name rather than one per harness, because what the board is saying is *a
machine is on this*, not which process: the running ones are already told apart
by their agent sessions and by the token each holds. So a deployment has one,
which :attr:`Person.is_agent` and its partial unique index make a fact about
the table rather than a convention.
"""

AGENT_TITLE = "Machine, worked through the API"
"""Its one line in the directory, which is also what the assignee picker shows
beside its name. It says what it is rather than repeating what it is called.
"""

AGENT_RESPONSIBILITIES = (
    "Works the cards it is given, through the API. It signs in as nobody: it "
    "carries a token minted by whoever runs it."
)


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
        # Partial, so the flag costs an index entry on one row rather than on
        # the whole directory, and so "there is one agent" is something the
        # schema holds rather than something every caller has to check first.
        Index("ix_person_is_agent", "is_agent", unique=True, postgresql_where=text("is_agent")),
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

    title: Mapped[str] = mapped_column(String(160), nullable=False)
    """Who this is, in one line — "Finance controller, Atlas".

    Called ``title`` rather than ``role`` since CYLIST-45, which gave the word
    role to a thing a project's admin creates and hands out. This is the other
    thing: a job description, written by whoever added the person, that no
    board has any opinion about.
    """

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

    is_agent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false"), default=False
    )
    """Whether this entry is the machine rather than a person.

    Set by :func:`app.services.people.ensure_agent` and by nothing else — not
    by ``POST /people``, which is how a colleague is added. Read so that the
    row can be found without matching on a name anybody may edit, and so that a
    screen can say which of the names on a board belongs to a bot.

    An agent is :attr:`PersonKind.TEAM`: it does the work, which is what that
    kind means. The line this draws is a different one — whether there is
    anybody behind the name.
    """

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
