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

    person_id: UUID | None
    """Which person in the directory is making this request.

    ``None`` only for the bootstrap session — somebody signing in with
    ``CYLIST_PASSWORD_HASH`` on a deployment that has no accounts yet, whose
    first job is to create one. Every other principal is somebody: a session
    is whoever signed in, and an API token is whoever minted it, so an agent
    assigns work and writes history under its owner's name rather than the
    server's.

    This is the whole of "who is asking". Cylist's projects are a shared
    workspace — everyone signed in sees the same boards — so there is no
    authorisation question hanging off this, only an attribution one: who
    ``is_me`` means, who joins a project they create, and whose name goes on
    the audit row.
    """

    label: str
    scopes: frozenset[Scope]
    channel: Channel

    def has(self, scope: Scope) -> bool:
        return scope in self.scopes

    def missing(self, required: frozenset[Scope]) -> frozenset[Scope]:
        """Return the required scopes this principal does not hold."""
        return required - self.scopes
