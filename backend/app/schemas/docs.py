"""Docs: a project's markdown, filed by section and topic."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.doc import TOPIC_NAME_MAX_LENGTH, DocSection
from app.schemas.common import Schema
from app.schemas.people import PersonRead
from app.services.commands.docs import (
    BODY_MAX_LENGTH as BODY_MAX_LENGTH,
)
from app.services.commands.docs import (
    FACT_MAX_LENGTH as FACT_MAX_LENGTH,
)
from app.services.commands.docs import (
    FACTS_MAX as FACTS_MAX,
)
from app.services.commands.docs import (
    AnswerStatus as AnswerStatus,
)
from app.services.commands.docs import (
    DocCreate as DocCreate,
)
from app.services.commands.docs import (
    DocPlace as DocPlace,
)
from app.services.commands.docs import (
    DocTopicCreate as DocTopicCreate,
)
from app.services.commands.docs import (
    DocUpdate as DocUpdate,
)
from app.services.commands.docs import (
    DocWrite as DocWrite,
)
from app.services.commands.docs import (
    FactIn as FactIn,
)
from app.services.commands.docs import (
    FactShape as FactShape,
)
from app.services.commands.docs import (
    _stripped as _stripped,
)


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


class DocWritten(Schema):
    doc: DocRead
    created: bool = Field(description="False when `append` added to a doc already there.")
    filed_by: Literal["caller", "only_topic"] = Field(
        description="Who chose the topic: the writer, or nobody because the project has only one."
    )
