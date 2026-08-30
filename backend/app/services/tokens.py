"""Issuing, listing and revoking credentials."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
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
    ttl: timedelta | None = None,
) -> tuple[ApiToken, str]:
    """Create a credential and return it with its one-time plaintext.

    The plaintext is the only copy; it is returned to the caller and then
    forgotten. Only its digest reaches the database.
    """
    plaintext, digest = generate_token()
    token = ApiToken(
        name=name,
        kind=kind,
        token_hash=digest,
        scopes=sorted(scope.value for scope in scopes),
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
    """
    result = await session.scalars(
        select(ApiToken)
        .where(ApiToken.kind == TokenKind.API, ApiToken.revoked_at.is_(None))
        .order_by(ApiToken.created_at.desc())
    )
    return list(result)


async def revoke(session: AsyncSession, token_id: UUID) -> ApiToken:
    """Mark a credential unusable. Revoking twice is not an error."""
    token = await get(session, token_id)
    if not token.is_revoked:
        token.revoked_at = now()
    return token


async def revoke_by_digest(session: AsyncSession, digest: str) -> None:
    """Revoke whichever credential has this digest, if it is still live.

    Used by logout, which holds the cookie value rather than the token id.
    """
    token = await session.scalar(select(ApiToken).where(ApiToken.token_hash == digest))
    if token is not None and not token.is_revoked:
        token.revoked_at = now()
