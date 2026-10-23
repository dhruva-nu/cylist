"""A project's roles, and what a member wears."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import Page, Schema
from app.services.commands.roles import (
    _COLOUR_PATTERN as _COLOUR_PATTERN,
)
from app.services.commands.roles import (
    RoleCreate as RoleCreate,
)
from app.services.commands.roles import (
    RoleUpdate as RoleUpdate,
)


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


class RolePage(Page[RoleSummary]):
    """A page of a project's roles, the admin role first."""
