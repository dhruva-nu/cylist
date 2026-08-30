"""Login and identity."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.models.activity import Channel
from app.schemas.common import Schema
from app.schemas.people import PersonRead


class LoginRequest(Schema):
    password: str = Field(min_length=1, description="The owner's password.")


class Identity(Schema):
    """Who the current credential belongs to and what it may do."""

    token_id: UUID
    label: str
    channel: Channel
    scopes: list[str]
    person: PersonRead | None = Field(
        default=None,
        description=(
            "The directory entry marked as you, if there is one. It is the "
            "person put on every project as it is created."
        ),
    )
