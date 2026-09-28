"""Docs: a project's markdown, filed by section and topic."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
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


# --- What agents read and write ------------------------------------------------


QUESTION_MAX_LENGTH = 1000
FACT_MAX_LENGTH = 2000
FACTS_MAX = 20

AnswerStatus = Literal["ok", "ambiguous", "unverified", "not_documented", "unavailable"]
FactShape = Literal["rule", "history", "contract", "mechanism", "lifecycle", "failure", "glossary"]
"""The section shapes of jev-docs' DOCS_FORMAT.md: a rule, history and a
contract lean product; a mechanism engineering; the rest either."""
DocShape = Literal["indexed", "headings", "bullets", "plain"]


class DocQuestion(Schema):
    question: str = Field(
        min_length=1,
        max_length=QUESTION_MAX_LENGTH,
        description="What you would otherwise open the code to find out, as you would ask a "
        'colleague: "where is the check that stops a sub-task being moved?"',
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        return _stripped(value)


class SectionFound(Schema):
    """One section of one doc, as the question was routed to it."""

    doc_id: UUID
    path: str = Field(description="`Engineering / MCP / tasks.md` — how `read_doc` takes it.")
    section: str | None = Field(description="The section's title; null for a whole doc.")
    text: str = Field(
        description="The section's markdown — or the whole doc's, when the "
        "question needs all of it."
    )
    whole_doc: bool
    relevance: float | None = Field(
        description="jev's probability, having read the section, that it answers the question."
    )


class Alternative(Schema):
    """A section the router weighed and did not choose."""

    doc_id: UUID
    path: str
    section: str | None
    score: float = Field(description="How likely the route was before the text was read.")
    relevance: float | None


class DocAnswer(Schema):
    """Where the docs answer a question, and how sure jev is that they do."""

    question: str
    status: AnswerStatus = Field(
        description="`ok`: `found` answers it — work from it. `ambiguous`: `found` passed the "
        "check but the route was close; glance at `alternatives`. `unverified`: no section "
        "passed the check, and `found` is only the best guess. `not_documented`: nothing "
        "written on it. `unavailable`: jev could not be asked — `reason` says why. Anything "
        "but `ok` and `ambiguous` means: find it in the code, then `place` it."
    )
    threshold: float = Field(description="The relevance an answer must reach to be `ok`.")
    found: SectionFound | None
    also: SectionFound | None = Field(
        description="When the question asks both why and how: the other side's section."
    )
    alternatives: list[Alternative]
    reason: str | None = Field(description="Why jev was not asked, or what it noted.")


class FactIn(Schema):
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


class DocPlace(Schema):
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


PlanKind = Literal[
    "new_file",
    "add_section",
    "update_section",
    "add_index_entry",
    "update_summary",
    "add_related",
    "split_file",
    "check_twin",
    "new_topic",
]


class PlannedEdit(Schema):
    """One edit the plan calls for. Nothing has been written."""

    kind: PlanKind = Field(
        description="`add_section` / `update_section`: into `doc_id`. `new_file`: a new doc "
        "under `topic_id`. `add_index_entry`, `update_summary`: keep `doc_id` findable. "
        "`add_related`: a `Related:` line in `doc_id` naming `related_path`. `new_topic`: no "
        "topic fits — topics are made by people, so file it in the nearest one and say so."
    )
    sure: bool = Field(description="False: worth a look, not worth doing blind.")
    probability: float
    why: str
    facts: list[int] = Field(description="Which facts it carries, by position from 0.")
    doc_id: UUID | None
    path: str | None = Field(
        description="The doc, `Engineering / MCP / tasks.md`, or the "
        "topic a new doc goes under, `Engineering / MCP`."
    )
    section: str | None = Field(description="The section to change, for `update_section`.")
    topic_id: UUID | None
    doc_shape: DocShape | None = Field(
        description="How the doc is written, so the edit matches it. `indexed`: an `## Index` "
        "entry `N. Title — blurb` and a matching `## Title` section. `headings`: a `## Title` "
        "section. `bullets`: one `- ` line. `plain`: anywhere."
    )
    related_path: str | None


class DocPlan(Schema):
    status: Literal["planned", "unavailable"]
    reason: str | None
    edits: list[PlannedEdit]


class DocWrite(Schema):
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


class DocWritten(Schema):
    doc: DocRead
    created: bool = Field(description="False when `append` added to a doc already there.")
    filed_by: Literal["caller", "only_topic"] = Field(
        description="Who chose the topic: the writer, or nobody because the project has only one."
    )
