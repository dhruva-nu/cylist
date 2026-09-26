"""Docs: a project's markdown, filed by section and topic."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.doc import TITLE_MAX_LENGTH, TOPIC_NAME_MAX_LENGTH, DocSection
from app.schemas.common import Schema
from app.schemas.people import PersonRead

BODY_MAX_LENGTH = 200_000
"""About forty pages of prose. Generous for a doc and still a ceiling, so one
runaway agent cannot write a megabyte into a row every tree read walks past."""


def _stripped(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


class DocTopicCreate(Schema):
    section: DocSection = Field(description="`product` or `engineering`.")
    name: str = Field(min_length=1, max_length=TOPIC_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _stripped(value)


class DocTopicUpdate(Schema):
    """Rename a topic. A topic does not change section: its docs were filed as
    product or engineering, and moving the heading would refile all of them."""

    name: str = Field(min_length=1, max_length=TOPIC_NAME_MAX_LENGTH)

    @field_validator("name")
    @classmethod
    def _strip_name(cls, value: str) -> str:
        return _stripped(value)


class DocTopicOrder(Schema):
    """Every topic in one section, in the order they should be drawn."""

    section: DocSection
    topic_ids: list[UUID] = Field(
        description="Every topic in the section, exactly once, top first."
    )


class DocOrder(Schema):
    """Every doc in one topic, in the order they should be drawn."""

    doc_ids: list[UUID] = Field(description="Every doc in the topic, exactly once, top first.")


class DocCreate(Schema):
    title: str = Field(min_length=1, max_length=TITLE_MAX_LENGTH)
    body: str = Field(default="", max_length=BODY_MAX_LENGTH, description="Markdown.")

    @field_validator("title")
    @classmethod
    def _strip_title(cls, value: str) -> str:
        return _stripped(value)


class DocUpdate(Schema):
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


class DocListing(Schema):
    """A doc as the tree draws it: everything but the body."""

    id: UUID
    topic_id: UUID
    title: str
    position: int
    author: PersonRead | None
    created_at: datetime
    updated_at: datetime


class DocRead(DocListing):
    """One doc, its markdown included, and where it is filed."""

    project_id: UUID
    section: DocSection
    topic_name: str
    body: str = Field(description="Markdown, as written.")


class DocTopicRead(Schema):
    id: UUID
    project_id: UUID
    section: DocSection
    name: str
    position: int
    doc_count: int


class DocTopicWithDocs(DocTopicRead):
    docs: list[DocListing]


class DocSectionTree(Schema):
    section: DocSection
    label: str = Field(description="`Product` or `Engineering` — the heading as drawn.")
    topics: list[DocTopicWithDocs]


class DocTree(Schema):
    """A project's docs, as the Docs page draws them.

    Both sections, always, in the same order — an empty section is still a
    heading somebody can add a topic under. Bodies are left out: the tree is
    for finding a doc, and `GET /docs/{id}` is for reading one.
    """

    sections: list[DocSectionTree]
    doc_count: int
