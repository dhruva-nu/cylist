"""Authentication and authorisation dependencies.

A request authenticates with either

* ``Authorization: Bearer cyl_…`` — scripts, the CLI, MCP servers, or
* the ``cylist_session`` HttpOnly cookie — the browser.

Both resolve to a row in ``api_token``, so there is one lookup path and one
place where revocation and expiry are enforced.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import timedelta

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.auth.scopes import Scope, parse_scopes
from app.auth.tokens import hash_token, looks_like_token
from app.core.clock import now
from app.core.errors import ForbiddenError, UnauthorizedError
from app.db import SessionDependency
from app.models.activity import Channel
from app.models.api_token import ApiToken, TokenKind

logger = logging.getLogger(__name__)

SESSION_COOKIE = "cylist_session"

_LAST_USED_RESOLUTION = timedelta(minutes=1)
"""``last_used_at`` is only refreshed once per minute per token, so a busy
client does not turn every read into a write."""


def _presented_credential(request: Request) -> tuple[str, Channel] | None:
    """Extract the credential and note which door it came through."""
    header = request.headers.get("Authorization")
    if header and header.lower().startswith("bearer "):
        return header[7:].strip(), Channel.API

    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie, Channel.WEB

    return None


async def current_principal(
    request: Request,
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Resolve the caller, or raise :class:`UnauthorizedError`.

    The error message is intentionally identical for "no credential",
    "unknown credential" and "revoked credential" so it cannot be used to
    confirm that a particular token exists.
    """
    presented = _presented_credential(request)
    if presented is None:
        _refused(request, "no credential presented")
        raise UnauthorizedError("Sign in or present an API token to use this endpoint.")

    plaintext, channel = presented
    if not looks_like_token(plaintext):
        _refused(request, "malformed credential", channel=channel.value)
        raise UnauthorizedError("That credential is not valid.")

    token = await session.scalar(
        select(ApiToken).where(ApiToken.token_hash == hash_token(plaintext))
    )
    if token is None:
        _refused(request, "unknown credential", channel=channel.value)
        raise UnauthorizedError("That credential is not valid.")
    if not token.is_usable:
        # Worth separating from "unknown" in the log even though the client is
        # told the same thing: a revoked or expired token is a caller who used
        # to be legitimate and now needs a new one — an operational problem —
        # whereas an unknown one is somebody guessing.
        _refused(
            request,
            "revoked or expired credential",
            channel=channel.value,
            token_id=str(token.id),
            label=token.name,
        )
        raise UnauthorizedError("That credential is not valid.")

    timestamp = now()
    if token.last_used_at is None or timestamp - token.last_used_at > _LAST_USED_RESOLUTION:
        token.last_used_at = timestamp

    return Principal(
        token_id=token.id,
        label=token.name,
        scopes=parse_scopes(token.scopes),
        channel=Channel.WEB if token.kind is TokenKind.SESSION else channel,
    )


def require(*scopes: Scope) -> Callable[[Principal], Awaitable[Principal]]:
    """Build a dependency that admits only principals holding every scope.

    Usage::

        @router.post("/tasks", dependencies=[Depends(require(Scope.WRITE))])
    """
    required = frozenset(scopes)

    async def dependency(principal: Principal = Depends(current_principal)) -> Principal:
        missing = principal.missing(required)
        if missing:
            logger.warning(
                "Token %r lacks the scopes for this endpoint",
                principal.label,
                extra={
                    "context": {
                        "token_id": str(principal.token_id),
                        "missing_scopes": sorted(scope.value for scope in missing),
                    }
                },
            )
            raise ForbiddenError(
                "This token is not allowed to perform that action.",
                details={"missing_scopes": sorted(scope.value for scope in missing)},
            )
        return principal

    return dependency


def _refused(request: Request, reason: str, **context: object) -> None:
    """Note an authentication failure, and why it really failed.

    The client is told the same thing whichever of these happened, so that a
    caller cannot use the error to discover whether a token exists. The log is
    not subject to that constraint — it is read by whoever runs the server,
    who is entitled to know — and without the distinction a support question
    about a token that "stopped working" is unanswerable.

    Note what is absent: the credential itself, and its hash, which is the
    very string the database is searched by. Neither belongs in a file that
    gets pasted into bug reports.
    """
    logger.warning(
        "Authentication refused: %s",
        reason,
        extra={"context": {"path": request.url.path, **context}},
    )
