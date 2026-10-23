"""API tokens for scripts, the CLI and MCP servers.

Managing credentials requires the ``admin`` scope, so a token issued to an
agent cannot mint itself a broader one. The one exception is a credential
revoking itself: giving up your own access widens nothing, and it is how the
phone app signs out with a token that, rightly, does not carry ``admin``.

Holding ``admin`` is not enough on its own to widen anything either
(CYLIST-117). A new token carries at most the scopes of whoever minted it, so
one leaked ``admin`` token is that token's authority and no more; and
``permit`` is only ever handed out from a browser, by a person who signed in.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_principal, require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import ForbiddenError
from app.db import SessionDependency
from app.models.activity import Channel, EntityType, Verb
from app.models.api_token import TokenKind
from app.routers.paging import PageQuery
from app.schemas.common import Acknowledged
from app.schemas.tokens import TokenCreate, TokenIssued, TokenPage, TokenRead
from app.services import activity, tokens
from app.services.paging import PageRequest

router = APIRouter(prefix="/tokens", tags=["tokens"])


@router.get("", response_model=TokenPage, summary="List API tokens")
async def list_tokens(
    _: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> TokenPage:
    """Return the live API tokens, newest first, a page at a time. Browser
    sessions are not listed."""
    found = await tokens.list_api_tokens(session, page)
    return TokenPage(
        items=[TokenRead.model_validate(token) for token in found.rows], next=found.next
    )


def _require_grantable(principal: Principal, scopes: list[Scope]) -> None:
    """Refuse a token broader than the credential asking for it.

    ``admin`` is what lets a caller mint at all; what it may mint is bounded
    by what it already holds. Without that, an agent-management token could
    write itself ``vault:reveal`` and ``permit``, and the narrowing that
    scopes exist for would last exactly one request.

    ``permit`` is narrower still: only a browser session may grant it. It is
    the scope that answers an agent's permission prompt, so a bearer token —
    which is what every agent holds — must never be the thing that hands it
    out, even a bearer token that carries it. The phone app is unaffected:
    it signs in with a session and mints its own token from there.

    And a caller must be somebody (CYLIST-119). A token acts as whoever
    minted it, so one minted by the bootstrap session would act as nobody —
    a credential no archive or password change could reach. The bootstrap
    session's job is to open the first account, and that needs no token.

    Raises:
        ForbiddenError: naming the scopes in ``details.missing_scopes``, or
            ``details.scope`` for ``permit`` asked for over the API, or when
            the caller has no person.
    """
    if principal.person_id is None:
        raise ForbiddenError(
            "A token acts as whoever mints it, and this credential is nobody's. "
            "Open an account under People and mint it from there."
        )
    missing = principal.missing(frozenset(scopes))
    if missing:
        raise ForbiddenError(
            "A token cannot be given scopes the credential minting it does not hold.",
            details={"missing_scopes": sorted(scope.value for scope in missing)},
        )
    if Scope.PERMIT in scopes and principal.channel is not Channel.WEB:
        raise ForbiddenError(
            "Only a person signed in to the web app can grant `permit`; "
            "an API token never can, so no agent can give itself one.",
            details={"scope": Scope.PERMIT.value, "channel": principal.channel.value},
        )


@router.post(
    "",
    response_model=TokenIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Issue an API token",
    responses={403: {"description": "A scope asked for is one the caller does not hold."}},
)
async def create_token(
    body: TokenCreate,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
) -> TokenIssued:
    """Mint a token and return its plaintext exactly once.

    Grant the narrowest useful set of scopes: an agent that only rearranges the
    board needs ``read`` and ``write``, never ``vault:reveal``.

    The token acts as whoever minted it. An agent moving a card writes that
    person's name into the card's history, which is the answer everybody
    wants to "who moved this?" — "an API token" never was, and with several
    people on one board it would be an answer that names nobody.

    Its scopes must be ones the caller holds, and `permit` can only be
    granted from a browser session.
    """
    _require_grantable(principal, body.scopes)
    ttl = timedelta(days=body.expires_in_days) if body.expires_in_days else None
    token, plaintext = await tokens.issue(
        session,
        name=body.name,
        kind=TokenKind.API,
        scopes=body.scopes,
        person_id=principal.person_id,
        ttl=ttl,
    )
    await activity.record(
        session,
        principal,
        Verb.TOKEN_ISSUED,
        entity_type=EntityType.TOKEN,
        entity_id=token.id,
        payload={"name": token.name, "scopes": token.scopes},
    )
    return TokenIssued(**TokenRead.model_validate(token).model_dump(), token=plaintext)


_require_admin = require(Scope.ADMIN)


async def _may_revoke(
    token_id: UUID,
    principal: Principal = Depends(current_principal),
) -> Principal:
    """Admit an ``admin`` caller, or any caller revoking the credential it is using."""
    if token_id == principal.token_id:
        return principal
    return await _require_admin(principal)


@router.delete("/{token_id}", response_model=Acknowledged, summary="Revoke an API token")
async def revoke_token(
    token_id: UUID,
    principal: Principal = Depends(_may_revoke),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Revoke a token immediately. Requests using it stop working at once.

    Needs ``admin``, except to revoke the very token making the request: any
    credential may sign itself out (the phone app does, with its own id from
    ``GET /me``).
    """
    token = await tokens.revoke(session, token_id)
    await activity.record(
        session,
        principal,
        Verb.TOKEN_REVOKED,
        entity_type=EntityType.TOKEN,
        entity_id=token.id,
        payload={"name": token.name},
    )
    return Acknowledged()
