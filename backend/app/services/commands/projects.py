"""The commands the projects services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.projects``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.models.project import KEY_MAX_LENGTH
from app.services.commands.base import Command

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9]{1,5}$"


class ProjectCreate(Command):
    key: str = Field(
        pattern=_KEY_PATTERN,
        description=(
            f"2 to {KEY_MAX_LENGTH} letters and digits, starting with a letter. "
            "Stored uppercase and usable in place of the id, e.g. /projects/ATL."
        ),
    )
    name: str = Field(min_length=1, max_length=160)
    description: str = Field(default="", max_length=2000)
    colour: str | None = Field(
        default=None,
        pattern=_COLOUR_PATTERN,
        description="Six-digit hex. Omit to take a stable colour from the palette.",
    )

    @field_validator("key")
    @classmethod
    def _upper(cls, value: str) -> str:
        return value.upper()

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()


class ProjectUpdate(Command):
    """Every field optional; omitted fields are left as they are."""

    key: str | None = Field(default=None, pattern=_KEY_PATTERN)
    name: str | None = Field(default=None, min_length=1, max_length=160)
    description: str | None = Field(default=None, max_length=2000)
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)
    archived: bool | None = Field(
        default=None, description="Set false to bring an archived project back."
    )

    @field_validator("key")
    @classmethod
    def _upper(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None
