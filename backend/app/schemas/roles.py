"""A project's roles, and what a member wears."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.role import NAME_MAX_LENGTH
from app.schemas.common import Schema

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"


class RoleCreate(Schema):
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


class RoleUpdate(Schema):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=2000)
    colour: str | None = Field(default=None, pattern=_COLOUR_PATTERN)


class RoleRead(Schema):
    """A role as it is worn — what a badge beside a name needs and no more."""

    id: UUID
    name: str
    description: str
    colour: str
    is_admin: bool = Field(
        description="Whether holders of this role may manage the project's roles."
    )


class RoleSummary(RoleRead):
    """A role on the list an admin manages it from."""

    member_count: int = Field(description="How many of the project's members hold it.")
    created_at: datetime


class MemberRoleUpdate(Schema):
    """Puts one of the project's roles on a member, or takes it off."""

    role: str | None = Field(
        default=None,
        description=(
            "The role's id, or its name (case-insensitive). Null takes their "
            "role off and leaves them a member with none."
        ),
    )
