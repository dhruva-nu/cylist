"""Issuing, listing and revoking credentials."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.scopes import Scope
from app.auth.tokens import generate_token
from app.core.clock import now
from app.core.errors import NotFoundError
from app.models.api_token import ApiToken, TokenKind


async def issue(
    session: AsyncSession,
    *,
    name: str,
    kind: TokenKind,
    scopes: Iterable[Scope],
    person_id: UUID | None,
    ttl: timedelta | None = None,
) -> tuple[ApiToken, str]:
    """Create a credential and return it with its one-time plaintext.

    The plaintext is the only copy; it is returned to the caller and then
    forgotten. Only its digest reaches the database.

    Args:
        person_id: Who the credential acts as — whoever signed in, or whoever
            minted it for an agent. Keyword-only and without a default on
            purpose: every caller has to have an answer, and the one correct
            ``None`` (the bootstrap session, before any account exists) is
            worth writing out at the call site rather than falling into.
    """
    plaintext, digest = generate_token()
    token = ApiToken(
        name=name,
        kind=kind,
        token_hash=digest,
        scopes=sorted(scope.value for scope in scopes),
        person_id=person_id,
        expires_at=now() + ttl if ttl is not None else None,
    )
    session.add(token)
    await session.flush()
    return token, plaintext


async def get(session: AsyncSession, token_id: UUID) -> ApiToken:
    """Fetch one credential, or raise :class:`NotFoundError`."""
    token = await session.get(ApiToken, token_id)
    if token is None:
        raise NotFoundError("No token with that id.")
    return token


async def list_api_tokens(session: AsyncSession) -> list[ApiToken]:
    """Return every non-revoked API token, newest first.

    Session cookies are excluded: they are an implementation detail of being
    logged in, not something to manage on a settings page.

    Everybody's, not just the caller's. Tokens are managed with the ``admin``
    scope, and an admin who can revoke a credential but cannot see it is an
    admin who cannot do the job the scope exists for. Which person each one
    acts as is on the row.
    """
    tokens = await session.scalars(
        select(ApiToken)
        .where(ApiToken.kind == TokenKind.API, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return list(tokens)


async def revoke(session: AsyncSession, token_id: UUID) -> ApiToken:
    """Mark a credential unusable. Revoking twice is not an error."""
    token = await get(session, token_id)
    if not token.is_revoked:
        token.revoked_at = now()
    return token


async def revoke_for_person(
    session: AsyncSession, person_id: UUID, *, keeping: UUID | None = None
) -> int:
    """Revoke every live credential belonging to one person, returning how many.

    What archiving somebody calls. Their sessions and their agents' tokens go
    at once, because "they have left" has to mean it before the cookie in
    their browser expires a month from now.

    A bulk UPDATE rather than a loop: this runs inside the request that
    archives them, and the number of tokens one person holds is not bounded by
    anything.

    Args:
        keeping: One credential to leave alone — the caller's own, where the
            caller is the person being revoked. An admin who sets their own
            password should not be signed out by the act of setting it, while
            every other browser they left themselves signed in on should.
    """
    live = [ApiToken.person_id == person_id, ApiToken.revoked_at.is_(None)]
    if keeping is not None:
        live.append(ApiToken.id != keeping)

    revoked = await session.execute(
        sql_update(ApiToken)
        .where(*live)
        .values(revoked_at=now())
        # Postgres counts what it touched for free; without this the caller
        # would have to SELECT first to be able to say how many went.
        .returning(ApiToken.id)
    )
    return len(revoked.all())


async def revoke_by_digest(session: AsyncSession, digest: str) -> None:
    """Revoke whichever credential has this digest, if it is still live.

    Used by logout, which holds the cookie value rather than the token id.
    """
    token = await session.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    if token is not None and not token.is_revoked:
        token.revoked_at = now()
