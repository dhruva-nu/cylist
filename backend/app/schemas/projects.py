"""Projects and their membership."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.common import Page, Schema
from app.schemas.people import PersonRead
from app.schemas.roles import RoleRead
from app.services.commands.projects import (
    _COLOUR_PATTERN as _COLOUR_PATTERN,
)
from app.services.commands.projects import (
    _KEY_PATTERN as _KEY_PATTERN,
)
from app.services.commands.projects import (
    ProjectCreate as ProjectCreate,
)
from app.services.commands.projects import (
    ProjectUpdate as ProjectUpdate,
)


class ProjectRead(Schema):
    id: UUID
    key: str
    name: str
    description: str
    colour: str
    archived_at: datetime | None
    created_at: datetime
    member_count: int = Field(description="How many people are on this project.")


class ProjectSummary(ProjectRead):
    """The numbers behind a project's hub cards.

    One request behind every card on a project's hub.
    """

    team_count: int
    client_count: int
    task_count: int = Field(
        description="Cards on the board. Sub-tasks are not among them — they are work on a "
        "card rather than cards — so this is the column counts added up."
    )
    column_count: int
    blocked_count: int = Field(
        description="Tasks that cannot proceed. Flagged red on the hub. Sub-tasks are counted "
        "here, unlike in `task_count`: a blocked sub-task is blocked work whether or not the "
        "board draws it."
    )
    on_hold_count: int = Field(description="Tasks deliberately paused, sub-tasks included.")

    goal_count: int = Field(description="Goals on the project, settled ones included.")
    open_goal_count: int = Field(
        description="Goals still being worked towards — neither achieved nor dropped."
    )

    folder_count: int
    file_count: int = Field(
        description="Everything in the project's folders — uploads and links alike."
    )

    doc_count: int = Field(description="Docs across both sections and every topic.")

    vault_tree_count: int
    vault_secret_count: int = Field(description="Credentials stored across every tree.")

    skill_count: int = Field(description="Skills uploaded for this project's agents.")


class MembershipUpdate(Schema):
    """Replaces a project's membership with exactly these people."""

    person_ids: list[UUID] = Field(
        description="The complete member list. People not named here are removed."
    )

    @field_validator("person_ids")
    @classmethod
    def _deduplicate(cls, value: list[UUID]) -> list[UUID]:
        seen: dict[UUID, None] = dict.fromkeys(value)
        return list(seen)


class MemberRead(PersonRead):
    """A person as they appear *on a project*, rather than in the directory.

    Everything a directory entry has, plus the one thing that only exists once
    somebody is on a board: what they are on it. ``role`` is that role and not
    the job title, which is :attr:`PersonRead.title` — the two were the same
    word until CYLIST-45 and are deliberately no longer.
    """

    role: RoleRead | None = Field(
        default=None,
        description="Their role on this project, or null if they have none yet.",
    )


class Membership(Schema):
    members: list[MemberRead]


class ProjectPage(Page[ProjectRead]):
    """A page of the caller's projects, newest first."""
