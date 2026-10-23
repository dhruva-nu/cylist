"""The commands the docs services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.docs``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.doc import TITLE_MAX_LENGTH, TOPIC_NAME_MAX_LENGTH, DocSection
from app.services.commands.base import Command

BODY_MAX_LENGTH = 200_000
"""About forty pages of prose. Generous for a doc and still a ceiling, so one
runaway agent cannot write a megabyte into a row every tree read walks past."""


def _stripped(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class DocTopicCreate(Command):
    section: DocSection = Field(description="`product` or `engineering`.")
    name: str = Field(min_length=1, max_length=TOPIC_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _stripped(value)


class DocCreate(Command):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str = Field(default="", max_length=BODY_MAX_LENGTH, description="Markdown.")

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _stripped(value)


class DocUpdate(Command):
    """Every field optional; omitted fields are left as they are.

    Moving a doc is setting ``topic_id`` — to another topic on the same
    project, in either section. It lands at ``position`` in its new topic, or
    at the bottom when that is left out.
    """

    title: str | None = Field(default=None, min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str | None = Field(default=None, max_length=BODY_MAX_LENGTH)
    topic_id: UUID | None = Field(default=None, description="Move the doc to this topic.")
    position: int | None = Field(
        default=None,
        ge=0,
        description="Where in its topic, counting from the top. Clamped to the topic's length.",
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str | None) -> str | None:
        return None if value is None else _stripped(value)


FACT_MAX_LENGTH = 2000


FACTS_MAX = 20


AnswerStatus = Literal["ok", "ambiguous", "unverified", "not_documented", "unavailable"]


FactShape = Literal["rule", "history", "contract", "mechanism", "lifecycle", "failure", "glossary"]
"""The section shapes of jev-docs' DOCS_FORMAT.md: a rule, history and a
contract lean product; a mechanism engineering; the rest either."""


class FactIn(Command):
    text: str = Field(
        min_length=1,
        max_length=FACT_MAX_LENGTH,
        description="One thing you found, as a sentence the next reader could act on.",
    )
    shape: FactShape | None = Field(
        default=None,
        description="What kind of fact it is; it steers which section it lands in.",
    )

    @field_validator("text")
    @classmethod
    def _strip_text(cls, value: str) -> str:
        return _stripped(value)


class DocPlace(Command):
    """What an agent found in the code, to be told where in the docs it goes."""

    title: str = Field(
        min_length=1, max_length=TITLE_MAX_LENGTH, description="A short name for what you found."
    )
    summary: str = Field(default="", max_length=FACT_MAX_LENGTH)
    entities: list[str] = Field(
        default_factory=list,
        max_length=FACTS_MAX,
        description="The names a reader would type to find it: `move_task`, `Book C`.",
    )
    facts: list[FactIn] = Field(min_length=1, max_length=FACTS_MAX)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _stripped(value)


class DocWrite(Command):
    """Write a doc onto a project, naming its topic or leaving it to be filed."""

    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str = Field(default="", max_length=BODY_MAX_LENGTH, description="Markdown.")
    topic_id: UUID | None = Field(
        default=None,
        description="The topic to file it under — the one `place` named. Leave it out only "
        "when the project has a single topic; otherwise the write is refused with the "
        "topics to choose from.",
    )
    append: bool = Field(
        default=False,
        description="When the topic already holds a doc of this title, add `body` to the end "
        "of it rather than refusing. How a line goes onto a topic's `learned.md`.",
    )

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _stripped(value)
