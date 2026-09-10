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

    token_id: UUID | None
    """The credential behind the caller, when there still is one.

    ``None`` only where the actor is reconstructed from something that
    outlived its token — an agent session whose row survived the key it was
    written with. ``activity.actor_token_id`` is a nullable foreign key for
    the same reason, and a made-up id would not satisfy it. Every principal
    that came from a presented credential has one.
    """

    label: str
    scopes: frozenset[Scope]
    channel: Channel

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def missing(self, required: frozenset[Scope]) -> frozenset[Scope]:
        """Return the required scopes this principal does not hold."""
        return required - self.scopes
