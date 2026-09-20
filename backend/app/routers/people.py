"""The people directory, and the accounts that hang off it.

One directory across every project: a client who works with you on three
projects is one entry, so their details are edited once.

Giving a team member an account is an invitation, not a password somebody
types on their behalf — see :func:`invite_person`. Taking it away is
archiving them, which is the same gesture as removing them from the pickers
because leaving is one event, not two.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.person import PersonKind
from app.schemas.common import Acknowledged
from app.schemas.people import InviteIssued, PersonCreate, PersonRead, PersonUpdate
from app.services import activity, people

router = APIRouter(prefix="/people", tags=["people"])


@router.get("", response_model=list[PersonRead], summary="List people")
async def list_people(
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    kind: PersonKind | None = Query(default=None, description="Filter to team or clients."),
    include_archived: bool = Query(default=False),
) -> list[PersonRead]:
    """Return the directory, team first and alphabetical within each kind."""
    found = await people.list_people(session, kind=kind, include_archived=include_archived)
    return [PersonRead.model_validate(person) for person in found]


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
        "person.added",
        entity_type="person",
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


@router.patch("/{person_id}", response_model=PersonRead, summary="Update a person")
async def update_person(
    person_id: UUID,
    body: PersonUpdate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> PersonRead:
    """Change any subset of a person's details. Omitted fields are left alone."""
    person = await people.update(session, person_id, body)
    await activity.record(
        session,
        principal,
        "person.updated",
        entity_type="person",
        entity_id=person.id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    return PersonRead.model_validate(person)


@router.delete("/{person_id}", response_model=Acknowledged, summary="Archive a person")
async def archive_person(
    person_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Archive someone rather than deleting them.

    Tasks they were assigned and files they uploaded keep naming them; they
    simply stop appearing in pickers. Bring them back with
    `PATCH /people/{id}` and `{"archived": false}`.
    """
    person = await people.archive(session, person_id)
    await activity.record(
        session,
        principal,
        "person.archived",
        entity_type="person",
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
) -> InviteIssued:
    """Mint a one-time link that lets somebody set their first password.

    Behind ``admin`` rather than ``write``: ``write`` is the scope an agent
    that shuffles cards is given, and handing out accounts is not something
    that should come with it.

    Cylist sends no email, so the link comes back here for you to pass on. It
    is shown once; ask again and the old one stops working.
    """
    person, token = await people.invite(session, person_id)
    await activity.record(
        session,
        principal,
        "person.invited",
        entity_type="person",
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
        "person.invite_withdrawn",
        entity_type="person",
        entity_id=person.id,
        payload={"name": person.name},
    )
    return Acknowledged()
