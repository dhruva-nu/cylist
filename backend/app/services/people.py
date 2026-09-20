"""The people directory: create, amend, archive — and who among them signs in.

Accounts live here rather than in a module of their own because an account is
not a separate thing from a directory entry: it is a directory entry that has
been given a password. The three functions that matter are :func:`invite`,
which mints the one-time link, :func:`accept_invite`, which turns it into a
password, and :func:`authenticate`, which is the only place a password is ever
checked against a row.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.invites import INVITE_TTL, generate_invite, looks_like_invite
from app.auth.passwords import hash_password, needs_rehash, verify_password
from app.auth.tokens import hash_token
from app.core.clock import now
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.palette import colour_for
from app.models.person import (
    AGENT_NAME,
    AGENT_RESPONSIBILITIES,
    AGENT_TITLE,
    KIND_ORDER,
    Person,
    PersonKind,
)
from app.schemas.people import PersonCreate, PersonUpdate
from app.services import tokens


async def create(session: AsyncSession, data: PersonCreate) -> Person:
    """Add someone to the directory."""
    person = Person(
        name=data.name,
        kind=data.kind,
        title=data.title,
        responsibilities=data.responsibilities,
        email=data.email,
        colour=data.colour or colour_for(data.name),
    )
    session.add(person)
    await session.flush()
    return person


async def agent(session: AsyncSession) -> Person | None:
    """The machine's directory entry, or ``None`` if there is not one yet.

    Found by the flag rather than by the name: the name is editable, and a
    board that renamed its agent "Claude" should not thereby have two.
    """
    found: Person | None = await session.scalar(select(Person).where(Person.is_agent))
    return found


async def ensure_agent(session: AsyncSession) -> Person:
    """Return the agent's entry, adding it to the directory if it is missing.

    Every deployment has one — migration ``0027`` writes it — so this finds it
    rather than creating it almost every time. It creates rather than assuming,
    because a card can be handed to the agent long before anybody thinks to
    check whether it is there, and because a schema built from the models
    (which is what the tests run against) has no migration behind it.

    Archived is left archived. Taking the agent off the boards is a thing
    somebody did on purpose, and un-doing it because a project was created
    would be the one way this could surprise anybody.
    """
    existing = await agent(session)
    if existing is not None:
        return existing

    machine = Person(
        name=AGENT_NAME,
        kind=PersonKind.TEAM,
        title=AGENT_TITLE,
        responsibilities=AGENT_RESPONSIBILITIES,
        email=None,
        colour=colour_for(AGENT_NAME),
        is_agent=True,
    )
    session.add(machine)
    await session.flush()
    return machine


async def get(session: AsyncSession, person_id: UUID) -> Person:
    """Fetch one person, archived or not."""
    person = await session.get(Person, person_id)
    if person is None:
        raise NotFoundError("No person with that id.")
    return person


async def list_people(
    session: AsyncSession,
    *,
    kind: PersonKind | None = None,
    include_archived: bool = False,
) -> list[Person]:
    """Return the directory, team first, alphabetical within each kind."""
    statement = select(Person).order_by(KIND_ORDER, Person.name)
    if kind is not None:
        statement = statement.where(Person.kind == kind)
    if not include_archived:
        statement = statement.where(Person.archived_at.is_(None))
    return list(await session.scalars(statement))


async def update(session: AsyncSession, person_id: UUID, data: PersonUpdate) -> Person:
    """Apply a partial update.

    ``archived`` is translated to a timestamp here rather than exposing
    ``archived_at`` for a client to set to an arbitrary moment.
    """
    person = await get(session, person_id)

    fields = data.model_dump(exclude_unset=True, exclude={"archived"})
    for field, value in fields.items():
        setattr(person, field, value)

    if data.archived is not None:
        person.archived_at = now() if data.archived else None

    if "email" in fields and (person.has_account or person.invite_is_pending):
        # Changing an account's email changes the name it signs in under, so
        # the uniqueness the login lookup depends on has to be re-checked. The
        # index would catch it at flush, but as an IntegrityError naming a
        # constraint rather than as a sentence about the address.
        await _require_email_free(session, person.email, excluding=person.id)

    await session.flush()
    return person


async def archive(session: AsyncSession, person_id: UUID) -> Person:
    """Hide someone from the pickers, keeping every reference to them intact."""
    person = await get(session, person_id)
    if not person.is_archived:
        person.archived_at = now()
    # Archiving is also how access is withdrawn, so it has to reach the
    # credentials: a session cookie outlives it by a month otherwise, and an
    # API token outlives it forever. The password is deliberately left alone —
    # un-archiving somebody should not mean they have to be invited again.
    await tokens.revoke_for_person(session, person.id)
    person.invite_token_hash = None
    person.invite_expires_at = None
    return person


# --- Accounts ---------------------------------------------------------------


async def has_any_account(session: AsyncSession) -> bool:
    """Whether anybody at all can sign in.

    False is the state a fresh deployment starts in, and the only state in
    which ``CYLIST_PASSWORD_HASH`` is accepted as a login. Archived people
    count here: a deployment whose one account has been archived has locked
    itself out, and quietly re-opening the bootstrap door would be a way back
    in that nobody asked for.
    """
    found = await session.scalar(
        select(Person.id).where(Person.password_hash.is_not(None)).limit(1)
    )
    return found is not None


async def authenticate(session: AsyncSession, email: str, password: str) -> Person | None:
    """Return the person this email and password belong to, or ``None``.

    The only place a stored password is checked. Folds case on the email,
    matching ``ix_person_account_email``, and refuses archived people — the
    same check the request path makes, made here too so that being archived
    stops a login rather than merely stopping the session it would issue.

    A correct password stored under outdated Argon2 parameters is re-hashed
    on the way through. This is the one moment the plaintext exists, so it is
    the only moment the upgrade can happen.
    """
    person = await session.scalar(
        select(Person).where(
            func.lower(Person.email) == email.strip().lower(),
            Person.password_hash.is_not(None),
        )
    )
    if person is None:
        # Verify against nothing anyway. Returning early here would make a
        # wrong email measurably faster to reject than a wrong password, which
        # is how an attacker learns which addresses have accounts.
        verify_password(password, None)
        return None
    if not verify_password(password, person.password_hash):
        return None
    if person.is_archived:
        return None
    if person.password_hash is not None and needs_rehash(person.password_hash):
        person.password_hash = hash_password(password)
    return person


async def invite(session: AsyncSession, person_id: UUID) -> tuple[Person, str]:
    """Open an account for somebody, returning them and the one-time token.

    Re-inviting is allowed and replaces the outstanding invitation: a link
    that was never opened is worth nothing, and refusing to send a second one
    would mean waiting a week for the first to expire.

    Raises:
        UnprocessableRequestError: if they are archived, are the agent, are a
            client, or have no email address to send it to.
        ConflictError: if they already have a password, or if their email
            belongs to somebody who can already sign in.
    """
    person = await get(session, person_id)
    if person.is_archived:
        raise UnprocessableRequestError("An archived person cannot be given an account.")
    if person.is_agent:
        raise UnprocessableRequestError(
            f"{person.name} is a machine, and a machine does not sign in. Mint it an API "
            "token instead — see Tokens."
        )
    if person.kind is not PersonKind.TEAM:
        raise UnprocessableRequestError(
            "Only team members get accounts. Clients are named on the work, not signed in to it."
        )
    if not person.email:
        raise UnprocessableRequestError(
            "Give them an email address first — it is what they sign in with."
        )
    if person.password_hash is not None:
        raise ConflictError(f"{person.name} already has an account.")

    await _require_email_free(session, person.email, excluding=person.id)

    plaintext, digest = generate_invite()
    person.invite_token_hash = digest
    person.invite_expires_at = now() + INVITE_TTL
    await session.flush()
    return person, plaintext


async def withdraw_invite(session: AsyncSession, person_id: UUID) -> Person:
    """Cancel an outstanding invitation. Doing it twice is not an error."""
    person = await get(session, person_id)
    person.invite_token_hash = None
    person.invite_expires_at = None
    await session.flush()
    return person


async def accept_invite(session: AsyncSession, token: str, password: str) -> Person:
    """Turn an invitation into a password, and return whose it was.

    Clearing the digest is what makes the link single-use; it happens in the
    same transaction as setting the password, so a failure part-way leaves the
    invitation still good rather than spent on nothing.

    Raises:
        UnprocessableRequestError: if the token is unknown, already used or
            expired.
    """
    if not looks_like_invite(token):
        raise UnprocessableRequestError("That invitation link is not valid.")

    person = await session.scalar(
        select(Person).where(Person.invite_token_hash == hash_token(token))
    )
    # One message for unknown, spent, expired and archived alike: an
    # invitation is a bare secret with no second factor, so what it is worth
    # saying about one is whether it works, not why it does not.
    if person is None or not person.invite_is_pending:
        raise UnprocessableRequestError("That invitation link is not valid, or it has expired.")

    person.password_hash = hash_password(password)
    person.invite_token_hash = None
    person.invite_expires_at = None
    await session.flush()
    return person


async def set_password(session: AsyncSession, person: Person, new_password: str) -> Person:
    """Replace somebody's password. The caller has already proved it is theirs."""
    person.password_hash = hash_password(new_password)
    person.invite_token_hash = None
    person.invite_expires_at = None
    await session.flush()
    return person


async def _require_email_free(session: AsyncSession, email: str | None, *, excluding: UUID) -> None:
    """Refuse an email already spoken for by somebody who can sign in.

    Mirrors ``ix_person_account_email``: the same rows, folded the same way.
    Checked here so the caller gets a sentence naming the address rather than
    an integrity error naming an index.
    """
    if not email:
        return
    clash = await session.scalar(
        select(Person.name).where(
            func.lower(Person.email) == email.strip().lower(),
            Person.id != excluding,
            (Person.password_hash.is_not(None)) | (Person.invite_token_hash.is_not(None)),
        )
    )
    if clash is not None:
        raise ConflictError(
            f"{email} is already the sign-in address for {clash}.",
            details={"email": email},
        )
