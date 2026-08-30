"""Projects and their membership."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.project import KEY_MAX_LENGTH
from app.schemas.common import Schema
from app.schemas.people import PersonRead

_COLOUR_PATTERN = r"^#(?:[0-9a-fA-F]{6})$"
_KEY_PATTERN = r"^[A-Za-z][A-Za-z0-9]{1,5}$"


class ProjectCreate(Schema):
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


class ProjectUpdate(Schema):
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

    Grows as later phases land: the file and vault counts join it when those
    tables exist.
    """

    team_count: int
    client_count: int
    task_count: int
    column_count: int
    blocked_count: int = Field(description="Tasks that cannot proceed. Flagged red on the hub.")
    on_hold_count: int = Field(description="Tasks deliberately paused.")


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


class Membership(Schema):
    members: list[PersonRead]
