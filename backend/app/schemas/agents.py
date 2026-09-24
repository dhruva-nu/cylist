"""A project's skills, and the lines on its agent scratchpad."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.agent import NOTE_MAX_LENGTH
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


class NoteRead(Schema):
    """One line on the scratchpad.

    ``author_label`` is the credential that wrote it — a token's name, or the
    owner for a browser session. It is what says whether a line came from an
    agent or from a person, which is most of what a reader wants to know
    before deciding whether to trust it.
    """

    id: UUID
    project_id: UUID
    body: str
    author_label: str
    added_by: PersonRead | None
    created_at: datetime


class NoteCreate(Schema):
    """Write something learned onto the scratchpad."""

    body: str = Field(
        min_length=1,
        max_length=NOTE_MAX_LENGTH,
        description=(
            f"What was learned, in at most {NOTE_MAX_LENGTH} characters. One fact per note, "
            "in as few words as carry it — the scratchpad is read in full by whoever comes "
            "next, so a paragraph here costs every later agent the time to read it. Write "
            "what a reader could not work out from the code or the board."
        ),
    )

    @field_validator("body")
    @classmethod
    def _one_tidy_line(cls, value: str) -> str:
        """Collapse a note to a single trimmed line.

        Newlines are the shape of prose, and prose is the thing this is not.
        Folding them into spaces rather than refusing them keeps a model that
        wrapped its own sentence from getting an error it cannot learn from,
        while still leaving one line in the database.
        """
        collapsed = " ".join(value.split())
        if not collapsed:
            raise ValueError("must not be blank")
        if len(collapsed) > NOTE_MAX_LENGTH:
            raise ValueError(f"must be at most {NOTE_MAX_LENGTH} characters")
        return collapsed
