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
from sqlalchemy import update as sql_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.invites import INVITE_TTL, generate_invite, looks_like_invite
from app.auth.passwords import dummy_hash, hash_password, needs_rehash, verify_password
from app.auth.principal import Principal
from app.auth.tokens import hash_token
from app.core.clock import now
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.palette import colour_for
from app.db import flush_or_refuse
from app.models.activity import Channel
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

    Raises:
        UnprocessableRequestError: if this archives the last person who can
            sign in.
    """
    person = await get(session, person_id)

    fields = data.model_dump(exclude_unset=True, exclude={"archived"})
    if "email" in fields and (
        person.password_hash is not None or person.invite_token_hash is not None
    ):
        # Changing an account's email changes the name it signs in under, so
        # the uniqueness the login lookup depends on has to be re-checked. The
        # index would catch it at flush, but as an IntegrityError naming a
        # constraint rather than as a sentence about the address. Asked of
        # every row the index holds — an archived account and a lapsed
        # invitation included — because the index does not know the
        # difference either; and asked before the address is written, so a
        # lapsed invitation it clears is gone before the index sees this row.
        await _require_email_free(session, fields["email"], excluding=person.id)

    for field, value in fields.items():
        setattr(person, field, value)

    if data.archived and not person.is_archived:
        await _require_another_way_in(session, person)
    if data.archived is not None:
        person.archived_at = now() if data.archived else None
    if data.archived:
        # Archiving through a PATCH is the same act as through a DELETE, and
        # leaves the same way: without credentials to come back to.
        await _withdraw_access(session, person)

    await flush_or_refuse(session, _email_taken(person.email))
    return person


async def archive(session: AsyncSession, person_id: UUID) -> Person:
    """Hide someone from the pickers, keeping every reference to them intact.

    Raises:
        UnprocessableRequestError: if they are the last person able to sign in.
    """
    person = await get(session, person_id)
    if not person.is_archived:
        await _require_another_way_in(session, person)
        person.archived_at = now()
    await _withdraw_access(session, person)
    return person


async def _require_another_way_in(session: AsyncSession, person: Person) -> None:
    """Refuse to archive the last person who can still sign in (CYLIST-118).

    Archiving takes every credential somebody holds, and
    :func:`has_any_account` counts archived accounts on purpose, so the
    bootstrap password does not come back either. Archiving the last live
    password would leave a deployment nobody can get into short of the
    database — which is not a thing any caller means by "archive".

    Raises:
        UnprocessableRequestError: naming them, if nobody else could sign in.
    """
    if person.password_hash is None:
        return
    another = await session.scalar(
        select(Person.id)
        .where(
            Person.password_hash.is_not(None),
            Person.archived_at.is_(None),
            Person.id != person.id,
        )
        .limit(1)
    )
    if another is None:
        raise UnprocessableRequestError(
            f"{person.name} is the last person who can sign in, and archiving them "
            "would lock everybody out. Open an account for somebody else first.",
            details={"person_id": str(person.id)},
        )


async def _withdraw_access(session: AsyncSession, person: Person) -> None:
    """Take away every way an archived person had of getting back in.

    Archiving is also how access is withdrawn, so it has to reach the
    credentials: a session cookie outlives it by a month otherwise, and an API
    token outlives it forever. The password is deliberately left alone —
    un-archiving somebody should not mean they have to be invited again.
    """
    await tokens.revoke_for_person(session, person.id)
    _clear_invitation(person)


def _clear_invitation(person: Person) -> None:
    """Spend whatever invitation is outstanding, so its link opens nothing."""
    person.invite_token_hash = None
    person.invite_expires_at = None


def _require_may_hold_account(person: Person) -> None:
    """Refuse an account to somebody who may not have one, however it is opened.

    The rules are about who may sign in, not about how: not archived, not the
    machine, and not a client.

    Raises:
        UnprocessableRequestError: naming which of those they are.
    """
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


async def is_bootstrap(session: AsyncSession, principal: Principal) -> bool:
    """Whether this caller is the bootstrap session, on a deployment still in need of it.

    Three things, all of them required (CYLIST-119). Nobody behind it, or it
    is a person. A browser session — ``Channel.WEB`` is what
    :func:`~app.auth.dependencies.resolve_principal` gives a session token —
    because the bootstrap password only ever buys one of those, and a
    person-less API token is a leftover, not the deployment's owner. And no
    account yet, because once one exists the bootstrap password no longer
    works and nothing it opened should either.

    Asked only where a caller has no person, so the query is not paid on the
    ordinary path.
    """
    return (
        principal.person_id is None
        and principal.channel is Channel.WEB
        and not await has_any_account(session)
    )


async def _close_the_bootstrap_door(session: AsyncSession) -> None:
    """Revoke the bootstrap credentials as the first account opens.

    :func:`has_any_account` already retires the bootstrap *password* the
    moment a password exists. This retires what it left behind: its session,
    which would otherwise last ``session_ttl_hours`` past that moment, and
    any token minted from it before minting from a person-less caller was
    refused. Checked before the password is written, since afterwards there
    is always an account.
    """
    if not await has_any_account(session):
        await tokens.revoke_personless(session)


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
        # Verify against a real hash anyway (CYLIST-121). Returning early here
        # would make a wrong email measurably faster to reject than a wrong
        # password, which is how an attacker learns which addresses have
        # accounts; and a check against nothing returns at once, so it has
        # to be a hash of a password nobody holds.
        await verify_password(password, dummy_hash())
        return None
    if not await verify_password(password, person.password_hash):
        return None
    if person.is_archived:
        return None
    if person.password_hash is not None and needs_rehash(person.password_hash):
        person.password_hash = await hash_password(password)
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
    _require_may_hold_account(person)
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
    await flush_or_refuse(session, _email_taken(person.email))
    return person, plaintext


async def open_account(
    session: AsyncSession, person_id: UUID, email: str, password: str
) -> tuple[Person, bool]:
    """Give somebody an account outright, and say whether they had one already.

    The same rules :func:`invite` applies, because they are rules about who may
    hold an account rather than about how one is opened: not archived, not the
    machine, not a client, and not an address somebody who signs in is already
    using. What it does not need is an email on the entry first — the address
    is part of the request here, so this sets it and signs them in with it in
    one go.

    Doing this to somebody who already has a password is a reset, which is half
    of why it exists: an invitation is no help to somebody who has lost the
    password they already set. The returned flag says which of the two
    happened, so the caller can decide what to revoke and what to record.

    Raises:
        UnprocessableRequestError: if they are archived, are the agent, or are
            a client.
        ConflictError: if the address belongs to somebody else who can sign in.
    """
    person = await get(session, person_id)
    _require_may_hold_account(person)
    await _require_email_free(session, email, excluding=person.id)

    had_one = person.password_hash is not None
    await _close_the_bootstrap_door(session)
    person.email = email
    person.password_hash = await hash_password(password)
    # Any outstanding invitation is answered by this: the account it would have
    # opened is open, and a link that still worked would be a second way in
    # that nobody is watching.
    _clear_invitation(person)
    await flush_or_refuse(session, _email_taken(email))
    return person, had_one


async def withdraw_invite(session: AsyncSession, person_id: UUID) -> Person:
    """Cancel an outstanding invitation. Doing it twice is not an error."""
    person = await get(session, person_id)
    _clear_invitation(person)
    await session.flush()
    return person


INVITE_NOT_VALID = "That invitation link is not valid, or it has expired."
"""One message for unknown, spent, expired and archived alike: an invitation
is a bare secret with no second factor, so what it is worth saying about one
is whether it works, not why it does not."""


async def accept_invite(session: AsyncSession, token: str, password: str) -> Person:
    """Turn an invitation into a password, and return whose it was.

    Clearing the digest is what makes the link single-use, and it is done in
    the one statement that sets the password: ``UPDATE … WHERE the invitation
    is still good RETURNING id``. Two accepts of one link at the same moment
    both pass a read, and a read-then-write would hand both of them a session
    with the second password winning (CYLIST-126). Of two conditional
    updates the second waits on the row, finds the digest gone, touches
    nothing, and is refused like a spent link. The read before it is only so
    that a dead link is refused before the hash is paid for.

    Raises:
        UnprocessableRequestError: if the token is unknown, already used or
            expired.
    """
    if not looks_like_invite(token):
        raise UnprocessableRequestError("That invitation link is not valid.")

    digest = hash_token(token)
    person = await session.scalar(select(Person).where(Person.invite_token_hash == digest))
    if person is None or not person.invite_is_pending:
        raise UnprocessableRequestError(INVITE_NOT_VALID)

    await _close_the_bootstrap_door(session)
    password_hash = await hash_password(password)
    consumed = await session.execute(
        sql_update(Person)
        .where(
            Person.invite_token_hash == digest,
            Person.invite_expires_at > now(),
            Person.archived_at.is_(None),
        )
        .values(password_hash=password_hash, invite_token_hash=None, invite_expires_at=None)
        .returning(Person.id)
        .execution_options(synchronize_session=False)
    )
    if consumed.scalar_one_or_none() is None:
        raise UnprocessableRequestError(INVITE_NOT_VALID)
    # The row in hand still shows the invitation; the statement went past it.
    await session.refresh(person)
    return person


async def set_password(session: AsyncSession, person: Person, new_password: str) -> Person:
    """Replace somebody's password. The caller has already proved it is theirs."""
    person.password_hash = await hash_password(new_password)
    _clear_invitation(person)
    await session.flush()
    return person


def _email_taken(email: str | None) -> ConflictError:
    """What :func:`_require_email_free` says, for the request that passed it and
    then lost the address to somebody else's at the index."""
    return ConflictError(
        f"{email} was just taken as somebody else's sign-in address.",
        details={"email": email},
    )


async def _require_email_free(session: AsyncSession, email: str | None, *, excluding: UUID) -> None:
    """Refuse an email already spoken for by somebody who can sign in, or soon will.

    Mirrors ``ix_person_account_email``: the same rows, folded the same way.
    Checked here so the caller gets a sentence naming the address rather than
    an integrity error naming an index.

    One of those rows is not really spoken for: somebody with no password whose
    invitation has lapsed. Their link already opens nothing — see
    :attr:`Person.invite_is_pending` — but the index cannot tell, because a
    partial index cannot ask what time it is. Leaving it would hold the address
    forever, so it is spent here instead, and flushed before the caller writes
    the address anywhere else so the index sees the order things happened in.

    Raises:
        ConflictError: if the address belongs to an account, archived or not,
            or to somebody with an invitation still waiting to be accepted.
    """
    if not email:
        return
    clashes = await session.scalars(
        select(Person).where(
            func.lower(Person.email) == email.strip().lower(),
            Person.id != excluding,
            (Person.password_hash.is_not(None)) | (Person.invite_token_hash.is_not(None)),
        )
    )
    lapsed = False
    for clash in clashes:
        if clash.password_hash is not None:
            raise ConflictError(
                f"{email} is already the sign-in address for {clash.name}.",
                details={"email": email},
            )
        if clash.invite_is_pending:
            # Said as an invitation, because the usual way here is somebody
            # added twice — and "sign-in address" would send whoever reads it
            # looking for an account that does not exist yet.
            raise ConflictError(
                f"{clash.name} has already been invited at {email}, and the invitation "
                "has not expired yet. Withdraw it, or re-invite them instead.",
                details={"email": email},
            )
        _clear_invitation(clash)
        lapsed = True
    if lapsed:
        await session.flush()
