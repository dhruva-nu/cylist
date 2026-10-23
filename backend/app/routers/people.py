"""The people directory, and the accounts that hang off it.

One directory across every project: a client who works with you on three
projects is one entry, so their details are edited once.

Giving a team member an account is an invitation wherever one will do — see
:func:`invite_person` — because a link they redeem themselves leaves the
password with nobody but them. :func:`set_account` is the other door, for
where that cannot reach: somebody standing next to you, a deployment with no
way to pass a link on, or a person locked out of the password they already
set. Taking an account away is archiving them, which is the same gesture as
removing them from the pickers, because leaving is one event and not two.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.db import SessionDependency
from app.models.activity import EntityType, Verb
from app.models.person import Person, PersonKind
from app.routers.paging import PageQuery
from app.schemas.common import Acknowledged
from app.schemas.people import (
    AccountSet,
    InviteIssued,
    PersonCreate,
    PersonPage,
    PersonRead,
    PersonUpdate,
)
from app.services import activity, people, tokens
from app.services.paging import PageRequest

router = APIRouter(prefix="/people", tags=["people"])


@router.get("", response_model=PersonPage, summary="List people")
async def list_people(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    kind: PersonKind | None = Query(default=None, description="Filter to team or clients."),
    include_archived: bool = Query(default=False),
    page: PageRequest = PageQuery,
) -> PersonPage:
    """Return the directory a page at a time, team first and alphabetical
    within each kind."""
    found = await people.list_people(session, page, kind=kind, include_archived=include_archived)
    return PersonPage(
        items=[PersonRead.model_validate(person) for person in found.rows], next=found.next
    )


@router.post(
    "", response_model=PersonRead, status_code=status.HTTP_201_CREATED, summary="Add a person"
)
async def create_person(
    body: PersonCreate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> PersonRead:
    """Add someone to the directory.

    Describe what they are responsible for: that is what tells you who to tag
    when a task is waiting on somebody.
    """
    person = await people.create(session, body)
    await activity.record(
        session,
        principal,
        Verb.PERSON_ADDED,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        payload={"name": person.name, "kind": person.kind.value},
    )
    return PersonRead.model_validate(person)


@router.get("/{person_id}", response_model=PersonRead, summary="Get a person")
async def get_person(
    person_id: UUID,
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> PersonRead:
    return PersonRead.model_validate(await people.get(session, person_id))


_require_admin = require(Scope.ADMIN)


def _holds_an_account(person: Person) -> bool:
    """Whether this entry is somebody who signs in, or has been asked to.

    Read off the columns rather than :attr:`Person.has_account`, which is
    false for an archived account — and bringing one of those back is exactly
    a change this has to catch.
    """
    return person.password_hash is not None or person.invite_token_hash is not None


async def _may_change_access(principal: Principal, person: Person, body: PersonUpdate) -> None:
    """Ask for ``admin`` where an edit reaches somebody's way in (CYLIST-118).

    ``write`` is the scope every agent token carries, and the directory sits
    outside roles, so this is the only fence there is. A display name or a
    title is ``write``'s business. Archiving and un-archiving are not: one
    revokes every credential the person holds, the other gives them back. Nor
    is the email of an account, which is the name it signs in under. Those
    sit behind ``admin``, with inviting somebody and setting their password.

    Raises:
        ForbiddenError: with ``details.missing_scopes``, via :func:`require`.
    """
    sent = body.model_dump(exclude_unset=True)
    archives = "archived" in sent and body.archived != person.is_archived
    readdresses = "email" in sent and body.email != person.email and _holds_an_account(person)
    if archives or readdresses:
        await _require_admin(principal)


@router.patch(
    "/{person_id}",
    response_model=PersonRead,
    summary="Update a person",
    responses={
        403: {"description": "Archiving, or changing a sign-in email, needs `admin`."},
        422: {"description": "They are the last person who can sign in."},
    },
)
async def update_person(
    person_id: UUID,
    body: PersonUpdate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> PersonRead:
    """Change any subset of a person's details. Omitted fields are left alone.

    Names, titles, responsibilities and colours need `write`. Archiving or
    un-archiving somebody, and changing the email of anybody who has an
    account or an invitation, need `admin` as well.
    """
    await _may_change_access(principal, await people.get(session, person_id), body)
    person = await people.update(session, person_id, body)

    # Archiving is written as itself, as `DELETE` writes it. "Changed a
    # person's archived" is a line nobody scanning for who took somebody's
    # access away would recognise.
    described = sorted(
        field for field in body.model_dump(exclude_unset=True) if field != "archived"
    )
    if described:
        await activity.record(
            session,
            principal,
            Verb.PERSON_UPDATED,
            entity_type=EntityType.PERSON,
            entity_id=person.id,
            payload={"fields": described},
        )
    if body.archived is not None:
        await activity.record(
            session,
            principal,
            Verb.PERSON_ARCHIVED if body.archived else Verb.PERSON_RESTORED,
            entity_type=EntityType.PERSON,
            entity_id=person.id,
            payload={"name": person.name},
        )
    return PersonRead.model_validate(person)


@router.delete(
    "/{person_id}",
    response_model=Acknowledged,
    summary="Archive a person",
    responses={
        403: {"description": "Archiving somebody needs `admin`."},
        422: {"description": "They are the last person who can sign in."},
    },
)
async def archive_person(
    person_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE, Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Archive someone rather than deleting them.

    Tasks they were assigned and files they uploaded keep naming them; they
    simply stop appearing in pickers. Bring them back with
    `PATCH /people/{id}` and `{"archived": false}`.

    Needs `admin`, because archiving revokes every credential they hold — and
    `write` is the scope an agent carries. The last person who can sign in
    cannot be archived at all.
    """
    person = await people.archive(session, person_id)
    await activity.record(
        session,
        principal,
        Verb.PERSON_ARCHIVED,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        payload={"name": person.name},
    )
    return Acknowledged()


@router.post(
    "/{person_id}/invite",
    response_model=InviteIssued,
    status_code=status.HTTP_201_CREATED,
    summary="Invite a team member to open an account",
    responses={
        409: {"description": "They already have an account, or the email is taken."},
        422: {"description": "They are archived, are a client, or have no email address."},
    },
)
async def invite_person(
    person_id: UUID,
    request: Request,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
    settings: Settings = Depends(app_settings),
) -> InviteIssued:
    """Mint a one-time link that lets somebody set their first password.

    Behind ``admin`` rather than ``write``: ``write`` is the scope an agent
    that shuffles cards is given, and handing out accounts is not something
    that should come with it.

    Cylist sends no email, so the link comes back here for you to pass on. It
    is shown once; ask again and the old one stops working.
    """
    person, token = await people.invite(session, person_id, valid_for=settings.invite_ttl)
    await activity.record(
        session,
        principal,
        Verb.PERSON_INVITED,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        # The token is emphatically not in here. The audit log is readable
        # with `read` alone, and an invitation is a credential.
        payload={"name": person.name, "email": person.email},
    )
    return InviteIssued(
        person=PersonRead.model_validate(person),
        token=token,
        url=f"{str(request.base_url).rstrip('/')}/invite/{token}",
        expires_at=person.invite_expires_at,
    )


@router.put(
    "/{person_id}/account",
    response_model=PersonRead,
    summary="Give somebody an account, or reset the one they have",
    responses={
        409: {"description": "That email belongs to somebody else who can sign in."},
        422: {"description": "They are archived, are the agent, or are a client."},
    },
)
async def set_account(
    person_id: UUID,
    body: AccountSet,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
) -> PersonRead:
    """Set the email and password somebody signs in with, on their behalf.

    Behind `admin` for the reason inviting is: `write` is what an agent that
    shuffles cards holds, and handing out accounts should not come with it.

    `POST /people/{id}/invite` is the better way wherever it can be used, since
    a link they redeem themselves means nobody else ever knows their password.
    This is for where it cannot: somebody standing next to you, a deployment
    with no way to pass a link on, or a person locked out of the password they
    already set. **Whoever calls this knows their password**, so tell them to
    change it under Account.

    On somebody who already had one this is a reset, and every other credential
    they hold goes — the sessions they left open elsewhere, and any API token
    minted under their name. The caller's own session survives, so an admin who
    does this to themselves stays where they are.
    """
    person, had_one = await people.open_account(session, person_id, body.email, body.password)
    if had_one:
        await tokens.revoke_for_person(session, person.id, keeping=principal.token_id)
    await activity.record(
        session,
        principal,
        Verb.PERSON_PASSWORD_RESET if had_one else Verb.PERSON_ACCOUNT_OPENED,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        # The password is emphatically not in here, for the reason an
        # invitation token is not: the trail is readable with `read` alone.
        payload={"name": person.name, "email": person.email},
    )
    return PersonRead.model_validate(person)


@router.delete(
    "/{person_id}/invite",
    response_model=Acknowledged,
    summary="Withdraw an outstanding invitation",
)
async def withdraw_invite(
    person_id: UUID,
    principal: Principal = Depends(require(Scope.ADMIN)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Cancel an invitation that has not been accepted. Twice is not an error."""
    person = await people.withdraw_invite(session, person_id)
    await activity.record(
        session,
        principal,
        Verb.PERSON_INVITE_WITHDRAWN,
        entity_type=EntityType.PERSON,
        entity_id=person.id,
        payload={"name": person.name},
    )
    return Acknowledged()
