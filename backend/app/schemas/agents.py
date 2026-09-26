"""A project's skills."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.schemas.common import Schema
from app.schemas.people import PersonRead


class SkillRead(Schema):
    """One skill in a project's listing.

    ``added_by`` is the person rather than their id, as in a file listing: the
    screen draws their avatar, so an id would make a second request per row
    inevitable.
    """

    id: UUID
    project_id: UUID
    name: str
    description: str | None
    size: int
    mime: str
    added_by: PersonRead | None
    created_at: datetime


class SkillUpdate(Schema):
    """Change a skill's description without re-uploading it."""

    description: str | None = Field(
        default=None,
        max_length=2000,
        description="One line on what the skill does. Send null to clear it.",
    )


class SkillFolderFile(Schema):
    """One file of a skill, as it is written under its folder."""

    path: str = Field(
        description="Relative to the skill's folder, '/'-separated: 'SKILL.md', 'scripts/setup.sh'."
    )
    encoding: Literal["utf-8", "base64"] = Field(
        description=(
            "How `content` is written: the text itself, or base64 for anything that is not UTF-8."
        )
    )
    content: str
    size: int = Field(description="Bytes, once decoded.")
    executable: bool = Field(description="Whether to mark it executable, as a zip entry can ask.")


class SkillFolderRead(Schema):
    """A skill laid out as the folder Claude Code loads it from.

    Write every file under `.claude/skills/<folder>/` in a repository, or
    `~/.claude/skills/<folder>/` for every session on the machine, and Claude
    Code finds it. `cylist skills pull` is what does that for you.
    """

    skill: SkillRead
    folder: str = Field(description="The directory name: lowercase letters, digits and hyphens.")
    files: list[SkillFolderFile] = Field(description="SKILL.md first, then the rest by path.")
