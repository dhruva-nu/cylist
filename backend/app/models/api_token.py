"""Credentials.

Both a browser session and an agent's API key are rows in this one table. That
keeps authentication to a single code path and gives sessions the same
revocation and audit story as tokens.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from sqlalchemy import DateTime, Enum, Index, String, Text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import now
from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


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
