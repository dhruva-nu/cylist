"""The authenticated caller."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.auth.scopes import Scope
from app.models.activity import Channel


@dataclass(frozen=True, slots=True)
class Principal:
    """Who is making the request, and what they are allowed to do.

    Constructed once per request from the presented credential. Immutable, so
    no handler can widen its own permissions part-way through.
    """

    token_id: UUID
    label: str
    scopes: frozenset[Scope]
    channel: Channel

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def missing(self, required: frozenset[Scope]) -> frozenset[Scope]:
        """Return the required scopes this principal does not hold."""
        return required - self.scopes
