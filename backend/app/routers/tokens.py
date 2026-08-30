"""API tokens for scripts, the CLI and MCP servers.

Managing credentials requires the ``admin`` scope, so a token issued to an
agent cannot mint itself a broader one.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import get_session
from app.models.api_token import TokenKind
from app.schemas.common import Acknowledged
from app.schemas.tokens import TokenCreate, TokenIssued, TokenRead
from app.services import activity, tokens

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("", response_model=list[TokenRead], summary="List API tokens")
async def list_tokens(
    _: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> list[TokenRead]:
    """Return every live API token. Browser sessions are not listed."""
    return [TokenRead.model_validate(token) for token in await tokens.list_api_tokens(session)]


@router.post(
    "",
    response_model=TokenIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API token",
)
async def create_token(
    body: TokenCreate,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> TokenIssued:
    """Mint a token and return its plaintext exactly once.

    Grant the narrowest useful set of scopes: an agent that only rearranges the
    board needs ``read`` and ``write``, never ``vault:reveal``.
    """
    ttl = timedelta(days=body.expires_in_days) if body.expires_in_days else None
    token, plaintext = await tokens.issue(
        session,
        name=body.name,
        kind=TokenKind.API,
        scopes=body.scopes,
        ttl=ttl,
    )
    await activity.record(
        session,
        principal,
        "token.issued",
        entity_type="token",
        entity_id=token.id,
        payload={"name": token.name, "scopes": token.scopes},
    )
    return TokenIssued(**TokenRead.model_validate(token).model_dump(), token=plaintext)


@router.delete("/{token_id}", response_model=Acknowledged, summary="Revoke an API token")
async def revoke_token(
    token_id: UUID,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = Depends(get_session),
) -> Acknowledged:
    """Revoke a token immediately. Requests using it stop working at once."""
    token = await tokens.revoke(session, token_id)
    await activity.record(
        session,
        principal,
        "token.revoked",
        entity_type="token",
        entity_id=token.id,
        payload={"name": token.name},
    )
    return Acknowledged()
