"""Credentials.

Both a browser session and an agent's API key are rows in this one table. That
keeps authentication to a single code path and gives sessions the same
revocation and audit story as tokens.

Every credential names the person it belongs to. A session names whoever
signed in; an API token names whoever minted it, which is what makes an agent
act *as* somebody rather than as the server. That is the whole of multi-user
authorisation in one column: the request already had to find this row, so
knowing who is asking costs nothing more.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.clock import now
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import Person


class TokenKind(StrEnum):
    """How the credential was obtained."""

    SESSION = "session"
    """Issued by password login; lives in an HttpOnly cookie and expires."""

    API = "api"
    """Issued deliberately for a script, the CLI or an MCP server."""


class ApiToken(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "api_token"
    __table_args__ = (
        Index("ix_api_token_token_hash", "token_hash", unique=True),
        Index("ix_api_token_kind_revoked_at", "kind", "revoked_at"),
        Index("ix_api_token_person_id", "person_id"),
    )

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[TokenKind] = mapped_column(
        Enum(
            TokenKind,
            name="token_kind",
            native_enum=False,  # a VARCHAR + CHECK, so adding a value needs no ALTER TYPE
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    """SHA-256 of the plaintext token, hex-encoded. The plaintext is shown once
    and never stored."""

    scopes: Mapped[list[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)

    person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="CASCADE"),
    )
    """Who this credential acts as.

    Nullable for one case, and it is not the legacy one: the credential held
    by whoever is bootstrapping a deployment that has no accounts in it yet.
    There is nobody for that session to be, and inventing a person row to
    satisfy a foreign key would put a name in the directory that no one chose.
    Every credential minted after the first account exists carries a person.

    ``CASCADE`` rather than ``SET NULL`` because a credential outliving its
    owner is a credential that has quietly been promoted to the bootstrap one.
    People are archived rather than deleted, so in practice this never fires;
    it is here so that the one way it could fire is not the dangerous way.
    """

    person: Mapped[Person | None] = relationship(lazy="joined")
    """Loaded with the token, because authenticating every request has to know
    whether the owner is still allowed in — one join, not a second query."""

    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_expired(self) -> bool:
        return self.expires_at is not None and self.expires_at <= now()

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_usable(self) -> bool:
        """True when the credential may still authenticate a request."""
        return not self.is_revoked and not self.is_expired
