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

Signing in is the one door a stranger can knock on, so it is the one that is
rate limited (CYLIST-121, :mod:`app.auth.throttle`) and the one whose
password check runs off the event loop (:mod:`app.auth.passwords`). A person
can see and end their own sessions under ``/me/sessions`` (CYLIST-143); a
login retires the session the browser was holding and keeps the person to
:data:`~app.services.tokens.MAX_SESSIONS` live ones.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import NoReturn
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_principal, require
from app.auth.passwords import verify_password
from app.auth.principal import Principal
from app.auth.scopes import ALL_SCOPES, Scope
from app.auth.throttle import SignInThrottle, account_key
from app.auth.tokens import hash_token
from app.config import Settings, app_settings
from app.core.errors import ForbiddenError, UnauthorizedError
from app.core.request_log import client_address
from app.db import SessionDependency
from app.models.activity import Channel, EntityType, Verb
from app.models.api_token import ApiToken, TokenKind
from app.models.person import Person
from app.schemas.auth import Identity, LoginRequest, SessionRead
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
        settings.session_cookie_name,
        value,
        max_age=int(timedelta(hours=settings.session_ttl_hours).total_seconds()),
        httponly=True,  # unreadable from JavaScript, so XSS cannot exfiltrate it
        secure=settings.is_deployed,  # staging and prod are HTTPS; dev and test are not
        samesite="lax",  # survives normal navigation, not cross-site form posts
        # The base path, so a dev slot's session goes to that slot alone.
        path=settings.session_cookie_path,
    )


async def _start_session(
    session: AsyncSession,
    request: Request,
    response: Response,
    settings: Settings,
    person: Person | None,
) -> tuple[Identity, Principal]:
    """Mint a session for this person, set the cookie, and say who they are.

    Shared by login and by accepting an invitation, which both end with
    somebody signed in: making the second of those hand back a cookie is what
    keeps "set your password" from being immediately followed by "now sign in
    with the password you just set".

    The session the browser was holding, if any, is revoked first (CYLIST-143):
    the cookie is about to be overwritten, and a session nothing holds any
    more is a session only a copy of the cookie could use. Then, once the new
    one exists, the person is held to :data:`~app.services.tokens.MAX_SESSIONS`
    live sessions by letting the oldest go.

    A session holds every scope: it is a person sitting at the browser. Narrow
    scopes are for API tokens, which are handed to other software.
    """
    replaced = await _retire_presented_session(session, request, settings)

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
    capped = await tokens.cap_sessions(session, person.id) if person is not None else 0

    principal = Principal(
        token_id=token.id,
        person_id=person.id if person is not None else None,
        label=label,
        scopes=ALL_SCOPES,
        channel=Channel.WEB,
    )
    await activity.record(session, principal, Verb.SESSION_STARTED, entity_type=EntityType.SESSION)
    logger.info(
        "Signed in",
        extra={
            "context": {
                "token_id": str(token.id),
                "person_id": str(person.id) if person is not None else None,
                "ttl_hours": settings.session_ttl_hours,
                "client": client_address(request),
                "replaced_session": replaced,
                "sessions_capped": capped,
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


async def _retire_presented_session(
    session: AsyncSession, request: Request, settings: Settings
) -> bool:
    """Revoke the session in the request's cookie, if there is a live one.

    Only a session: an API token pasted into the cookie is not this
    browser's to give up. Returns whether anything was revoked.
    """
    cookie = request.cookies.get(settings.session_cookie_name)
    if not cookie:
        return False
    return await tokens.revoke_by_digest(session, hash_token(cookie), kind=TokenKind.SESSION)


def _throttle(request: Request, *, account: str | None) -> None:
    """Charge this attempt to the address it came from, and to the account.

    Raises:
        TooManyRequestsError: a 429 with ``Retry-After``, before any password
            is hashed.
    """
    throttle: SignInThrottle = request.app.state.sign_in_throttle
    throttle.charge(address=client_address(request), account=account)


@router.post(
    "/auth/login",
    response_model=Identity,
    summary="Sign in",
    responses={
        401: {"description": "Wrong email or password."},
        429: {"description": "Too many attempts; `Retry-After` says how long to wait."},
    },
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

    The session the browser already held, if any, is revoked; and if this
    person now holds more than ten live sessions, the oldest are revoked too.
    """
    _throttle(request, account=account_key(body.email))

    if body.email is None:
        # The bootstrap door, shut for good once somebody is behind it.
        if await people.has_any_account(session) or not await verify_password(
            body.password, settings.password_hash
        ):
            _refuse(request, body)
        person = None
    else:
        person = await people.authenticate(session, body.email, body.password)
        if person is None:
            _refuse(request, body)

    identity, _ = await _start_session(session, request, response, settings, person)
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
                "client": client_address(request),
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
    responses={
        422: {"description": "The invitation is unknown, spent or expired."},
        429: {"description": "Too many attempts; `Retry-After` says how long to wait."},
    },
)
async def accept_invite(
    body: InviteAccept,
    request: Request,
    response: Response,
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> Identity:
    """Turn an invitation link into an account, and sign in with it.

    Unauthenticated by necessity: the whole point is that whoever holds the
    link has no credential yet. The link *is* the credential, which is why it
    is one-time, expiring, and stored only as a digest — and why a link
    accepted twice at the same moment opens exactly one account.
    """
    _throttle(request, account=None)
    person = await people.accept_invite(session, body.token, body.password)
    identity, principal = await _start_session(session, request, response, settings, person)
    await activity.record(
        session,
        principal,
        Verb.PERSON_ACCOUNT_OPENED,
        entity_type=EntityType.PERSON,
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
    request: Request,
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
        # 403, as `today` and `devices` answer the same caller: it is who is
        # asking that is refused, not the request (CYLIST-146).
        raise ForbiddenError(
            "The bootstrap session has no password of its own to change. "
            "Create an account under People first."
        )

    person = await people.get(session, principal.person_id)
    if not await verify_password(body.current_password, person.password_hash):
        logger.warning(
            "Failed password change",
            extra={"context": {"person_id": str(person.id), "client": client_address(request)}},
        )
        raise UnauthorizedError("That is not your current password.")

    await people.set_password(session, person, body.new_password)
    await tokens.revoke_for_person(session, person.id)
    await _start_session(session, request, response, settings, person)

    # Recorded against the principal that made the request — the session just
    # revoked. That is the credential that actually did it, and saying so is
    # the point of an audit trail.
    await activity.record(
        session,
        principal,
        Verb.PERSON_PASSWORD_CHANGED,
        entity_type=EntityType.PERSON,
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
    settings: Settings = Depends(app_settings),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Revoke the current session and clear its cookie.

    Safe to call without a session; it simply clears the cookie.
    """
    cookie = request.cookies.get(settings.session_cookie_name)
    if cookie:
        await tokens.revoke_by_digest(session, hash_token(cookie))
    response.delete_cookie(settings.session_cookie_name, path=settings.session_cookie_path)
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


# --- A person's own sessions (CYLIST-143) -----------------------------------


def _require_person(principal: Principal) -> UUID:
    """The person behind this credential, or 403 for the bootstrap session —
    who is asking is what is refused, as ``POST /auth/password`` answers it."""
    if principal.person_id is None:
        raise ForbiddenError(
            "The bootstrap session belongs to nobody and has no sessions to manage. "
            "Create an account under People first."
        )
    return principal.person_id


def _session_read(token: ApiToken, principal: Principal) -> SessionRead:
    return SessionRead(
        id=token.id,
        created_at=token.created_at,
        expires_at=token.expires_at,
        last_used_at=token.last_used_at,
        channel=Channel.WEB,
        current=token.id == principal.token_id,
    )


@router.get(
    "/me/sessions",
    response_model=list[SessionRead],
    summary="List your own sessions",
    responses={403: {"description": "The bootstrap session has no person behind it."}},
)
async def my_sessions(
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[SessionRead]:
    """Every live browser session of the person this credential acts as,
    newest first, with when each was last used and which one this is.

    Only sessions. API tokens are listed under ``GET /tokens``.
    """
    person_id = _require_person(principal)
    return [
        _session_read(token, principal) for token in await tokens.list_sessions(session, person_id)
    ]


@router.delete(
    "/me/sessions/{session_id}",
    response_model=Acknowledged,
    summary="End one of your own sessions",
    responses={
        403: {"description": "The bootstrap session has no person behind it."},
        404: {"description": "No live session of yours with that id."},
    },
)
async def end_my_session(
    session_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Revoke one session; the browser holding it is signed out at once.

    Yours alone: somebody else's session, or a session already ended, is a
    404 either way, so the endpoint cannot be used to find out which ids
    exist. Ending the session making the request is allowed — it is a logout.
    """
    person_id = _require_person(principal)
    token = await tokens.revoke_session(session, person_id, session_id)
    await activity.record(
        session,
        principal,
        Verb.SESSION_ENDED,
        entity_type=EntityType.SESSION,
        entity_id=token.id,
        payload={"current": token.id == principal.token_id},
    )
    logger.info(
        "Session ended by its owner",
        extra={"context": {"token_id": str(token.id), "person_id": str(person_id)}},
    )
    return Acknowledged()
