"""API token management."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.auth.scopes import Scope
from app.schemas.common import Schema


class TokenCreate(Schema):
    name: str = Field(
        min_length=1,
        max_length=120,
        description="What this token is for, e.g. 'board-tidy agent'.",
    )
    scopes: list[Scope] = Field(
        min_length=1,
        description="Grant the narrowest set that still lets the client do its job.",
    )
    expires_in_days: int | None = Field(
        default=None, gt=0, le=3650, description="Omit for a token that never expires."
    )

    @field_validator("scopes")
    @classmethod
    def _deduplicate(cls, value: list[Scope]) -> list[Scope]:
        return sorted(set(value), key=lambda scope: scope.value)


class TokenRead(Schema):
    id: UUID
    name: str
    scopes: list[str]
    created_at: datetime
    expires_at: datetime | None
    last_used_at: datetime | None


class TokenIssued(TokenRead):
    """A freshly minted token, including the only copy of its plaintext."""

    token: str = Field(description="Copy this now — it is never shown again.")
