"""Login and identity."""

from __future__ import annotations

from uuid import UUID

from pydantic import EmailStr, Field

from app.models.activity import Channel
from app.schemas.common import Schema
from app.schemas.people import PersonRead


class LoginRequest(Schema):
    """An email and a password — or, before anybody has an account, just the
    password.

    ``email`` is optional for exactly one case: the first sign-in to a
    deployment that has no accounts in it, which is checked against
    ``CYLIST_PASSWORD_HASH`` and belongs to nobody. ``GET /setup`` says
    whether that is where this server is, so the sign-in screen knows which
    of the two forms to draw.
    """

    email: EmailStr | None = Field(
        default=None, description="Omit only while the deployment has no accounts yet."
    )
    password: str = Field(min_length=1)


class Identity(Schema):
    """Who the current credential belongs to and what it may do."""

    token_id: UUID
    label: str
    channel: Channel
    scopes: list[str]
    person: PersonRead | None = Field(
        default=None,
        description=(
            "Who this credential acts as: whoever signed in, or whoever "
            "minted the token an agent is using. Null only for the bootstrap "
            "session on a deployment with no accounts in it yet, whose first "
            "job is to create one."
        ),
    )
