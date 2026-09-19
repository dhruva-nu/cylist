"""What each kind of person on a project may do, as the screen reads it."""

from __future__ import annotations

from uuid import UUID

from pydantic import Field

from app.auth.permissions import Permission
from app.schemas.common import Schema


class PermissionInfoRead(Schema):
    """One permission, described well enough to draw a labelled tick box."""

    key: Permission
    label: str = Field(description="The column heading — a couple of words.")
    summary: str = Field(description="What ticking it allows.")


class RolePermissions(Schema):
    """One line of the grid: a role, and what it allows."""

    role_id: UUID | None = Field(
        description=(
            "The role these permissions are for, or null for everybody here "
            "who has no role — and for anyone not on the project at all."
        )
    )
    name: str = Field(description="The role's name, or 'Everyone else' for the baseline.")
    colour: str
    is_admin: bool = Field(
        description=(
            "Whether this is the admin role. An admin holds every permission "
            "by being one, so its line is fixed rather than configured."
        )
    )
    member_count: int
    permissions: list[Permission]


class ProjectPermissions(Schema):
    """The whole grid, plus the words to draw it with."""

    catalogue: list[PermissionInfoRead] = Field(
        description="Every permission Cylist recognises, in the order to draw them."
    )
    roles: list[RolePermissions] = Field(
        description="Admin first, then the project's other roles, then everyone else."
    )
    mine: list[Permission] = Field(
        description="What the caller may do here, which is what a client hides buttons by."
    )
    may_manage: bool = Field(
        description=(
            "Whether the caller may change any of this. Answered here rather "
            "than worked out from the grid, because three different callers "
            "may — the admin role's holders, the bootstrap session, and an "
            "`admin`-scoped credential on a project whose last admin was "
            "archived — and a client that reimplemented the rule would be "
            "wrong about at least one of them."
        )
    )


class PermissionsUpdate(Schema):
    """Replaces what one role allows.

    The whole set every time rather than a delta: the screen has the whole row
    in front of it, and a delta is how two admins on two tabs end up with the
    union of what each of them meant.
    """

    permissions: list[Permission] = Field(
        description="Exactly what this role may do afterwards. An empty list allows nothing."
    )
