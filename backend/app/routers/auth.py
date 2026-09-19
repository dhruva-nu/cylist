"""Signing in and out of the web app, and setting a password for the first time.

Login exchanges a person's email and password for a session credential
delivered as an HttpOnly cookie. The cookie's value is an ordinary token row
naming that person, so logging out revokes it, archiving them revokes it, and
it shows up in the audit trail like any other credential.

There are two ways in, and only ever one of them at a time:

* **A person's own password**, checked against ``person.password_hash``. This
  is how everybody signs in, once anybody has an account.
* **The bootstrap password**, checked against ``CYLIST_PASSWORD_HASH``, and
  accepted *only* while no person in the directory has a password at all. It
  is how a fresh deployment is opened for the first time, and it stops working
  the moment the first account exists — not by a flag anybody has to remember
  to set, but because the condition it depends on stops being true.

Accounts themselves are opened by invitation (``POST /people/{id}/invite``)
and closed by archiving the person. Nobody signs themselves up.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import NoReturn

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import SESSION_COOKIE, current_principal
from app.auth.passwords import verify_password
from app.auth.principal import Principal
from app.auth.scopes import ALL_SCOPES
from app.auth.tokens import hash_token
from app.config import Settings, app_settings
from app.core.errors import UnauthorizedError, UnprocessableRequestError
from app.db import SessionDependency
from app.models.activity import Channel
from app.models.api_token import TokenKind
from app.models.person import Person
from app.schemas.auth import Identity, LoginRequest
from app.schemas.common import Acknowledged
from app.schemas.people import InviteAccept, PasswordChange, PersonRead
from app.services import activity, people, tokens

logger = logging.getLogger(__name__)

router = APIRouter(tags=["auth"])

BOOTSTRAP_LABEL = "Bootstrap session"
"""What a session belonging to nobody is called in the audit trail.

It reads as what it is. Every other session is labelled with a person's name,
so a feed with this in it is a feed showing the one login that has no name
behind it — which is exactly the line somebody auditing a deployment should
stop on.
"""


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


async def _start_session(
    session: AsyncSession,
    response: Response,
    settings: Settings,
    person: Person | None,
) -> tuple[Identity, Principal]:
    """Mint a session for this person, set the cookie, and say who they are.

    Shared by login and by accepting an invitation, which both end with
    somebody signed in: making the second of those hand back a cookie is what
    keeps "set your password" from being immediately followed by "now sign in
    with the password you just set".

    A session holds every scope: it is a person sitting at the browser. Narrow
    scopes are for API tokens, which are handed to other software.
    """
    label = person.name if person is not None else BOOTSTRAP_LABEL
    token, plaintext = await tokens.issue(
        session,
        name=label,
        kind=TokenKind.SESSION,
        scopes=ALL_SCOPES,
        person_id=person.id if person is not None else None,
        ttl=timedelta(hours=settings.session_ttl_hours),
    )
    _set_session_cookie(response, plaintext, settings)

    principal = Principal(
        token_id=token.id,
        person_id=person.id if person is not None else None,
        label=label,
        scopes=ALL_SCOPES,
        channel=Channel.WEB,
    )
    await activity.record(session, principal, "session.started", entity_type="session")
    logger.info(
        "Signed in",
        extra={
            "context": {
                "token_id": str(token.id),
                "person_id": str(person.id) if person is not None else None,
                "ttl_hours": settings.session_ttl_hours,
            }
        },
    )

    identity = Identity(
        token_id=token.id,
        label=label,
        channel=Channel.WEB,
        scopes=sorted(token.scopes),
        person=PersonRead.model_validate(person) if person is not None else None,
    )
    return identity, principal


@router.post(
    "/auth/login",
    response_model=Identity,
    summary="Sign in",
    responses={401: {"description": "Wrong email or password."}},
)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Exchange an email and password for a session cookie.

    Omitting the email asks for the bootstrap login, which is refused unless
    the directory holds nobody who can sign in. See the module note.
    """
    if body.email is None:
        # The bootstrap door, shut for good once somebody is behind it.
        if await people.has_any_account(session) or not verify_password(
            body.password, settings.password_hash
        ):
            _refuse(request, body)
        person = None
    else:
        person = await people.authenticate(session, body.email, body.password)
        if person is None:
            _refuse(request, body)

    identity, _ = await _start_session(session, response, settings, person)
    return identity


def _refuse(request: Request, body: LoginRequest) -> NoReturn:
    """Turn every way of failing to sign in into the same answer.

    One message and one status for a wrong address, a wrong password, an
    archived account, and a bootstrap attempt on a deployment that has
    outgrown it. The log separates them, because whoever runs the server is
    entitled to know which it was; the response must not, or it becomes a way
    to find out who has an account here.
    """
    logger.warning(
        "Failed sign-in attempt",
        extra={
            "context": {
                "client": request.client.host if request.client else None,
                "email": body.email,
                "bootstrap": body.email is None,
            }
        },
    )
    raise UnauthorizedError("That email and password do not match an account.")


@router.post(
    "/auth/accept-invite",
    response_model=Identity,
    summary="Set a password from an invitation",
    responses={422: {"description": "The invitation is unknown, spent or expired."}},
)
async def accept_invite(
    body: InviteAccept,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Turn an invitation link into an account, and sign in with it.

    Unauthenticated by necessity: the whole point is that whoever holds the
    link has no credential yet. The link *is* the credential, which is why it
    is one-time, expiring, and stored only as a digest.
    """
    person = await people.accept_invite(session, body.token, body.password)
    identity, principal = await _start_session(session, response, settings, person)
    await activity.record(
        session,
        principal,
        "person.account_opened",
        entity_type="person",
        entity_id=person.id,
        payload={"name": person.name},
    )
    return identity


@router.post(
    "/auth/password",
    response_model=Acknowledged,
    summary="Change your own password",
    responses={401: {"description": "The current password is wrong."}},
)
async def change_password(
    body: PasswordChange,
    response: Response,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Acknowledged:
    """Replace your own password, proving you know the current one.

    Every other credential you hold is revoked — that is the point of changing
    a password under suspicion — and this session is re-issued, so the browser
    you did it in stays signed in and every other one does not.
    """
    if principal.person_id is None:
        raise UnprocessableRequestError(
            "The bootstrap session has no password of its own to change. "
            "Create an account under People first."
        )

    person = await people.get(session, principal.person_id)
    if not verify_password(body.current_password, person.password_hash):
        logger.warning(
            "Failed password change",
            extra={"context": {"person_id": str(person.id)}},
        )
        raise UnauthorizedError("That is not your current password.")

    await people.set_password(session, person, body.new_password)
    await tokens.revoke_for_person(session, person.id)
    await _start_session(session, response, settings, person)
    # Recorded against the principal that made the request — the session just
    # revoked. That is the credential that actually did it, and saying so is
    # the point of an audit trail.

    await activity.record(
        session,
        principal,
        "person.password_changed",
        entity_type="person",
        entity_id=person.id,
        payload={"name": person.name},
    )
    return Acknowledged()


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
    logger.info("Signed out", extra={"context": {"had_session": bool(cookie)}})
    return Acknowledged()


@router.get("/me", response_model=Identity, summary="Describe the current credential")
async def me(
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> Identity:
    """Return who the caller is and which scopes they hold.

    Useful to a CLI or MCP server for checking a token before it starts work,
    and to the web app, which asks once and then knows which person in every
    list it is looking at is itself.
    """
    person = await people.get(session, principal.person_id) if principal.person_id else None
    return Identity(
        token_id=principal.token_id,
        label=principal.label,
        channel=principal.channel,
        scopes=sorted(scope.value for scope in principal.scopes),
        person=PersonRead.model_validate(person) if person is not None else None,
    )
