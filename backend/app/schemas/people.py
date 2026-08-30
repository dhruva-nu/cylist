"""The people directory."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import EmailStr, Field, field_validator

from app.models.person import PersonKind
from app.schemas.common import Schema

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


class PersonCreate(Schema):
    name: str = Field(min_length=1, max_length=120)
    kind: PersonKind = Field(description="`team` does the work; `client` approves or unblocks it.")
    role: str = Field(
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

    @field_validator("name", "role", "responsibilities")
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
    role: str | None = Field(default=None, min_length=1, max_length=160)
    responsibilities: str | None = Field(default=None, min_length=1)
    email: EmailStr | None = None
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)
    archived: bool | None = Field(
        default=None, description="Set false to bring an archived person back."
    )


class PersonRead(Schema):
    id: UUID
    name: str
    kind: PersonKind
    role: str
    responsibilities: str
    email: str | None
    colour: str
    archived_at: datetime | None
    created_at: datetime
