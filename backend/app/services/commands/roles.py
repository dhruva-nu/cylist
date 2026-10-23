"""The commands the roles services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.roles``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from pydantic import Field, field_validator

from app.models.role import NAME_MAX_LENGTH
from app.services.commands.base import Command

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


class RoleCreate(Command):
    name: str = Field(
        min_length=1,
        max_length=NAME_MAX_LENGTH,
        description="What this role is called on this board — e.g. 'Reviewer'.",
    )
    description: str = Field(
        default="",
        max_length=2000,
        description="What it means here, in the admin's own words. Optional.",
    )
    colour: str | None = Field(
        default=None,
        pattern=_COLOUR_PATTERN,
        description="Six-digit hex. Omit to take a stable colour from the palette.",
    )

    @field_validator("name", "description")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("name")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value:
            raise ValueError("must not be blank")
        return value


class RoleUpdate(Command):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)
