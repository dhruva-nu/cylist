"""The commands the people services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.people``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import EmailStr, Field, field_validator

from app.models.person import PersonKind
from app.services.commands.base import Command

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


class PersonCreate(Command):
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


class PersonUpdate(Command):
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
