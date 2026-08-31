"""Signing in and out of the web app.

Login exchanges the owner's password for a session credential delivered as an
HttpOnly cookie. The cookie's value is an ordinary token row, so logging out
revokes it and it shows up in the audit trail like any other credential.
"""

from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE, current_principal
from app.auth.passwords import verify_password
from app.auth.principal import Principal
from app.auth.scopes import ALL_SCOPES
from app.auth.tokens import hash_token
from app.config import Settings, app_settings
from app.core.errors import UnauthorizedError
from app.db import SessionDependency
from app.models.activity import Channel
from app.models.api_token import TokenKind
from app.schemas.auth import Identity, LoginRequest
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.services import activity, people, tokens

router = APIRouter(tags=["auth"])


def _set_session_cookie(response: Response, value: str, settings: Settings) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        value,
        max_age=int(timedelta(hours=settings.session_ttl_hours).total_seconds()),
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.is_deployed,  # staging and prod are HTTPS; dev and test are not
        samesite="lax",  # survives normal navigation, not cross-site form posts
        path="/",
    )


@router.post(
    "/auth/login",
    response_model=Identity,
    summary="Sign in as the owner",
    responses={401: {"description": "Wrong password."}},
)
async def login(
    body: LoginRequest,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Exchange the owner's password for a session cookie.

    A session holds every scope: it is the owner sitting at the browser. Narrow
    scopes are for API tokens, which are handed to other software.
    """
    if not verify_password(body.password, settings.password_hash):
        raise UnauthorizedError("That password is not correct.")

    token, plaintext = await tokens.issue(
        session,
        name="Web session",
        kind=TokenKind.SESSION,
        scopes=ALL_SCOPES,
        ttl=timedelta(hours=settings.session_ttl_hours),
    )
    _set_session_cookie(response, plaintext, settings)

    principal = Principal(
        token_id=token.id,
        label=token.name,
        scopes=ALL_SCOPES,
        channel=Channel.WEB,
    )
    await activity.record(session, principal, "session.started", entity_type="session")

    return Identity(
        token_id=token.id,
        label=token.name,
        channel=principal.channel,
        scopes=sorted(token.scopes),
        person=await _me_person(session),
    )


@router.post(
    "/auth/logout",
    response_model=Acknowledged,
    summary="Sign out",
    status_code=status.HTTP_200_OK,
)
async def logout(
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Revoke the current session and clear its cookie.

    Safe to call without a session; it simply clears the cookie.
    """
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        await tokens.revoke_by_digest(session, hash_token(cookie))
    response.delete_cookie(SESSION_COOKIE, path="/")
    return Acknowledged()


async def _me_person(session: AsyncSession) -> PersonRead | None:
    """The directory entry marked as you, if anybody is."""
    person = await people.get_me(session)
    return PersonRead.model_validate(person) if person is not None else None


@router.get("/me", response_model=Identity, summary="Describe the current credential")
async def me(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> Identity:
    """Return who the caller is and which scopes they hold.

    Useful to a CLI or MCP server for checking a token before it starts work.
    The `person` field is the directory entry marked as you — what the web app
    puts in the top bar, and who joins every project you create.
    """
    return Identity(
        token_id=principal.token_id,
        label=principal.label,
        channel=principal.channel,
        scopes=sorted(scope.value for scope in principal.scopes),
        person=await _me_person(session),
    )
