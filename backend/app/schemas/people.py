"""The people directory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.models.person import PersonKind
from app.schemas.common import Schema

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"

MIN_PASSWORD_LENGTH = 12
"""Long rather than complicated.

No character-class rules: they push people towards ``Password1!`` and have
been off the OWASP and NIST recommendations for years. Length is the part that
actually buys entropy, and twelve is the floor both of them land on.
"""

MAX_PASSWORD_LENGTH = 256
"""A ceiling, because Argon2 will happily spend real CPU on a megabyte."""


class PersonCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    kind: PersonKind = Field(description="`team` does the work; `client` approves or unblocks it.")
    title: str = Field(
        min_length=1,
        max_length=160,
        description="Who this is, in one line — e.g. 'Finance controller, Atlas'.",
    )
    responsibilities: str = Field(
        min_length=1,
        description="What they do, and so what you would tag them about when work stalls.",
    )
    email: EmailStr | None = None
    colour: str | None = Field(
        default=None,
        pattern=_COLOUR_PATTERN,
        description="Six-digit hex. Omit to take a stable colour from the palette.",
    )

    @field_validator("name", "title", "responsibilities")
    @classmethod
    def _strip(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be blank")
        return stripped


class PersonUpdate(Schema):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=120)
    kind: PersonKind | None = None
    title: str | None = Field(default=None, min_length=1, max_length=160)
    responsibilities: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)
    archived: bool | None = Field(
        default=None,
        description=(
            "Archive them: they leave the pickers and every credential they "
            "hold is revoked. Set false to bring them back, password intact."
        ),
    )


class PersonRead(Schema):
    """One directory entry, the same for everybody who asks.

    There is deliberately no ``is_me`` here. It used to be a column, because
    there used to be one owner; with several people signed in it would have to
    mean something different in each response, and a payload whose truth
    depends on who fetched it is a payload that cannot be cached, shared or
    reasoned about. Whoever is asking already knows who they are — ``GET /me``
    told them — so "is this me?" is one comparison on the client and no
    ambiguity on the server.
    """

    id: UUID
    name: str
    kind: PersonKind
    title: str
    responsibilities: str
    email: str | None
    colour: str
    archived_at: datetime | None
    created_at: datetime

    has_account: bool = Field(
        description="Whether they can sign in as themselves right now.",
    )
    invite_is_pending: bool = Field(
        description="Whether an unaccepted, unexpired invitation is outstanding.",
    )


class InviteIssued(Schema):
    """A freshly minted invitation. The token is shown once and never again.

    Cylist does not send email, so the link is handed back to whoever asked
    for it to pass on. That is a deliberate stopping point rather than an
    omission: a secret that travels by a channel the sender chose is a secret
    somebody watched leave.
    """

    person: PersonRead
    token: str = Field(description="The one-time invitation token. Store nothing; send it once.")
    url: str = Field(description="The full link to give them, built from the request's origin.")
    expires_at: datetime


class InviteAccept(Schema):
    token: str = Field(min_length=1, description="The invitation token from the link.")
    password: str = Field(
        min_length=MIN_PASSWORD_LENGTH,
        max_length=MAX_PASSWORD_LENGTH,
        description="The password they will sign in with from now on.",
    )


class PasswordChange(Schema):
    current_password: str = Field(min_length=1)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=MAX_PASSWORD_LENGTH)
