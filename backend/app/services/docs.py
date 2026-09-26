"""Docs: filing a project's markdown under its topics, and keeping them in order.

Two rules here are choices rather than mechanics:

* **a topic with docs in it cannot be deleted** — see :func:`delete_topic`.
  The docs are the work and the topic is only a heading over them, so the
  refusal names how many would go and leaves moving them to whoever asked;
* **positions are always compacted.** Every reorder, move and delete rewrites
  the affected list as ``0..n-1``, so a position is never a gap somebody has
  to reason about and "put it at 2" always means third from the top.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.models.doc import SECTION_ORDER, Doc, DocSection, DocTopic
from app.models.project import Project
from app.models.task import Task
from app.schemas.docs import BODY_MAX_LENGTH, DocCreate, DocTopicCreate, DocUpdate, DocWrite
from app.services.doc_judge import (
    Card,
    DocJudge,
    DocOnFile,
    JudgeUnavailableError,
    TopicOnFile,
    summary_of,
)

logger = logging.getLogger(__name__)


async def topics_for(session: AsyncSession, project_id: UUID) -> list[DocTopic]:
    """Every topic on the project, section by section, top to bottom."""
    found = await session.scalars(
        select(DocTopic).where(DocTopic.project_id == project_id).order_by(DocTopic.position)
    )
    by_section = sorted(found, key=lambda topic: SECTION_ORDER.index(topic.section))
    return by_section


async def docs_for(session: AsyncSession, project_id: UUID) -> list[Doc]:
    """Every doc on the project, in topic order, without their bodies — the
    tree is for finding a doc, and a body is only read one doc at a time."""
    return list(
        await session.scalars(
            select(Doc)
            .options(defer(Doc.body))
            .where(Doc.project_id == project_id)
            .order_by(Doc.position, Doc.created_at)
        )
    )


async def count_for_project(session: AsyncSession, project: Project) -> int:
    counted = await session.scalar(
        select(func.count()).select_from(Doc).where(Doc.project_id == project.id)
    )
    return int(counted or 0)


async def counts_by_topic(session: AsyncSession, project_id: UUID) -> dict[UUID, int]:
    """How many docs each of the project's topics holds. Empty topics are absent."""
    rows = await session.execute(
        select(Doc.topic_id, func.count())
        .where(Doc.project_id == project_id)
        .group_by(Doc.topic_id)
    )
    return dict(rows.tuples().all())


async def get_topic(session: AsyncSession, topic_id: UUID) -> DocTopic:
    topic = await session.get(DocTopic, topic_id)
    if topic is None:
        raise NotFoundError("No doc topic with that id.")
    return topic


async def get_doc(session: AsyncSession, doc_id: UUID) -> Doc:
    doc = await session.get(Doc, doc_id)
    if doc is None:
        raise NotFoundError("No doc with that id.")
    return doc


async def find_topic(
    session: AsyncSession, project_id: UUID, section: DocSection, name: str
) -> DocTopic | None:
    """The section's topic by that name, however it was typed.

    For callers holding a name rather than an id — an agent told to file under
    "Engineering → DB schema".
    """
    found: DocTopic | None = await session.scalar(
        select(DocTopic).where(
            DocTopic.project_id == project_id,
            DocTopic.section == section,
            func.lower(DocTopic.name) == name.strip().casefold(),
        )
    )
    return found


# --- Topics ------------------------------------------------------------------


async def _section_topics(
    session: AsyncSession, project_id: UUID, section: DocSection
) -> list[DocTopic]:
    return list(
        await session.scalars(
            select(DocTopic)
            .where(DocTopic.project_id == project_id, DocTopic.section == section)
            .order_by(DocTopic.position, DocTopic.created_at)
        )
    )


async def _refuse_taken_name(
    session: AsyncSession,
    project_id: UUID,
    section: DocSection,
    name: str,
    *,
    except_id: UUID | None = None,
) -> None:
    clash = await find_topic(session, project_id, section, name)
    if clash is not None and clash.id != except_id:
        raise ConflictError(
            f"{section.label} already has a topic called {clash.name!r}.",
            details={"topic_id": str(clash.id)},
        )


async def create_topic(session: AsyncSession, project: Project, data: DocTopicCreate) -> DocTopic:
    """Add a topic at the bottom of its section.

    Raises:
        ConflictError: if the section already has a topic by that name.
    """
    await _refuse_taken_name(session, project.id, data.section, data.name)
    siblings = await _section_topics(session, project.id, data.section)
    topic = DocTopic(
        project_id=project.id,
        section=data.section,
        name=data.name,
        position=len(siblings),
    )
    session.add(topic)
    await session.flush()
    return topic


async def rename_topic(session: AsyncSession, topic: DocTopic, name: str) -> DocTopic:
    await _refuse_taken_name(session, topic.project_id, topic.section, name, except_id=topic.id)
    topic.name = name
    await session.flush()
    return topic


async def reorder_topics(
    session: AsyncSession, project: Project, section: DocSection, topic_ids: Sequence[UUID]
) -> list[DocTopic]:
    """Put a section's topics in exactly this order.

    Raises:
        UnprocessableRequestError: unless ``topic_ids`` names every topic in
            the section once — a partial order is a request to lose track of
            the topics it left out.
    """
    siblings = await _section_topics(session, project.id, section)
    _refuse_partial_order([topic.id for topic in siblings], topic_ids, "topic")
    by_id = {topic.id: topic for topic in siblings}
    for position, topic_id in enumerate(topic_ids):
        by_id[topic_id].position = position
    await session.flush()
    return [by_id[topic_id] for topic_id in topic_ids]


async def delete_topic(session: AsyncSession, topic: DocTopic) -> None:
    """Delete an empty topic and close the gap it leaves in its section.

    Raises:
        ConflictError: while any doc is still filed under it.
    """
    held = await session.scalar(
        select(func.count()).select_from(Doc).where(Doc.topic_id == topic.id)
    )
    if held:
        raise ConflictError(
            f"{topic.name!r} still has {held} doc{'s' if held != 1 else ''} in it. "
            "Move or delete them first.",
            details={"doc_count": held},
        )
    project_id, section = topic.project_id, topic.section
    await session.delete(topic)
    await session.flush()
    _compact(await _section_topics(session, project_id, section))
    await session.flush()


# --- Docs --------------------------------------------------------------------


async def _topic_docs(session: AsyncSession, topic_id: UUID) -> list[Doc]:
    return list(
        await session.scalars(
            select(Doc).where(Doc.topic_id == topic_id).order_by(Doc.position, Doc.created_at)
        )
    )


async def create_doc(
    session: AsyncSession, topic: DocTopic, data: DocCreate, *, author_id: UUID | None
) -> Doc:
    """File a new doc at the bottom of a topic."""
    siblings = await _topic_docs(session, topic.id)
    doc = Doc(
        project_id=topic.project_id,
        topic_id=topic.id,
        title=data.title,
        body=data.body,
        position=len(siblings),
        author_id=author_id,
    )
    session.add(doc)
    await session.flush()
    await session.refresh(doc, ["author", "created_at", "updated_at"])
    return doc


async def update_doc(session: AsyncSession, doc: Doc, data: DocUpdate) -> Doc:
    """Change a doc's words, or move it — within its topic or to another.

    Raises:
        UnprocessableRequestError: if ``topic_id`` names no topic on the doc's
            own project.
    """
    fields = data.model_dump(exclude_unset=True)
    if fields.get("title") is not None:
        doc.title = fields["title"]
    if fields.get("body") is not None:
        doc.body = fields["body"]

    destination = fields.get("topic_id") or doc.topic_id
    moving = destination != doc.topic_id
    if moving:
        topic = await session.get(DocTopic, destination)
        if topic is None or topic.project_id != doc.project_id:
            raise UnprocessableRequestError(
                "That topic is not on this doc's project.",
                details={"topic_id": str(destination)},
            )
    if moving or fields.get("position") is not None:
        await _place(session, doc, destination, fields.get("position"))

    await session.flush()
    await session.refresh(doc, ["author", "updated_at"])
    return doc


async def _place(session: AsyncSession, doc: Doc, topic_id: UUID, position: int | None) -> None:
    """Put ``doc`` at ``position`` in ``topic_id`` and compact both lists."""
    origin = doc.topic_id
    arrivals = [other for other in await _topic_docs(session, topic_id) if other.id != doc.id]
    index = len(arrivals) if position is None else min(position, len(arrivals))
    arrivals.insert(index, doc)
    doc.topic_id = topic_id
    _compact(arrivals)
    if origin != topic_id:
        _compact([other for other in await _topic_docs(session, origin) if other.id != doc.id])


async def reorder_docs(
    session: AsyncSession, topic: DocTopic, doc_ids: Sequence[UUID]
) -> list[Doc]:
    """Put a topic's docs in exactly this order.

    Raises:
        UnprocessableRequestError: unless ``doc_ids`` names every doc in the
            topic once.
    """
    siblings = await _topic_docs(session, topic.id)
    _refuse_partial_order([doc.id for doc in siblings], doc_ids, "doc")
    by_id = {doc.id: doc for doc in siblings}
    for position, doc_id in enumerate(doc_ids):
        by_id[doc_id].position = position
    await session.flush()
    ordered = [by_id[doc_id] for doc_id in doc_ids]
    for doc in ordered:
        # `updated_at` is expired by the flush that moved it; reading it back
        # here keeps the response from lazy-loading outside the event loop.
        await session.refresh(doc, ["updated_at"])
    return ordered


async def delete_doc(session: AsyncSession, doc: Doc) -> None:
    topic_id = doc.topic_id
    await session.delete(doc)
    await session.flush()
    _compact(await _topic_docs(session, topic_id))
    await session.flush()


# --- What agents read -----------------------------------------------------------

SUMMARY_SOURCE_CHARS = 2000
"""How much of each body is read to find its opening paragraph. A summary is a
sentence or two; loading every doc's whole body to find it would not be."""


@dataclass(frozen=True)
class RankedDoc:
    doc: Doc
    topic: DocTopic
    summary: str
    probability: float | None


@dataclass(frozen=True)
class Relevance:
    ranked_by: Literal["jev", "none"]
    reason: str | None
    docs: list[RankedDoc]
    doc_count: int


async def _filed(session: AsyncSession, project_id: UUID) -> list[RankedDoc]:
    """Every doc on the project in tree order, each with its topic and summary."""
    topics = await topics_for(session, project_id)
    by_topic: dict[UUID, list[Doc]] = {topic.id: [] for topic in topics}
    for doc in await docs_for(session, project_id):
        by_topic[doc.topic_id].append(doc)
    openings = dict(
        (
            await session.execute(
                select(Doc.id, func.left(Doc.body, SUMMARY_SOURCE_CHARS)).where(
                    Doc.project_id == project_id
                )
            )
        )
        .tuples()
        .all()
    )
    return [
        RankedDoc(doc=doc, topic=topic, summary=summary_of(openings[doc.id]), probability=None)
        for topic in topics
        for doc in by_topic[topic.id]
    ]


async def relevant_to(
    session: AsyncSession, task: Task, judge: DocJudge | None, *, threshold: float
) -> Relevance:
    """The docs work on ``task`` needs, as jev ranks them.

    One jev request for the whole card: the card is the state, and every doc
    is a yes/no question of its own. The docs jev puts at or past
    ``threshold`` come back most likely first.

    When jev cannot be asked — no key, no answer — every doc comes back in
    tree order instead. An agent handed too much can skim; one handed nothing
    because a model was down would start without what it needed.
    """
    filed = await _filed(session, task.project_id)

    def everything(reason: str | None) -> Relevance:
        return Relevance(ranked_by="none", reason=reason, docs=filed, doc_count=len(filed))

    if not filed:
        return everything(None)
    if judge is None:
        return everything("jev is not configured on this server.")

    card = Card(
        reference=task.reference,
        title=task.title,
        description=task.description or "",
        checklist=[item.title for item in task.checklist],
    )
    candidates = [
        DocOnFile(
            id=entry.doc.id,
            title=entry.doc.title,
            section_label=entry.topic.section.label,
            topic_name=entry.topic.name,
            summary=entry.summary,
        )
        for entry in filed
    ]
    try:
        probabilities = await judge.relevance(card, candidates)
    except JudgeUnavailableError as exc:
        logger.warning(
            "jev could not rank a card's docs; handing over the whole tree",
            extra={"context": {"task": task.reference, "error": str(exc)}},
        )
        return everything(f"jev could not be asked: {exc}")

    ranked = sorted(
        (
            RankedDoc(entry.doc, entry.topic, entry.summary, probabilities[entry.doc.id])
            for entry in filed
            if probabilities.get(entry.doc.id, 0.0) >= threshold
        ),
        key=lambda entry: -(entry.probability or 0.0),
    )
    return Relevance(ranked_by="jev", reason=None, docs=ranked, doc_count=len(filed))


# --- What agents write -----------------------------------------------------------


class TopicUnclearError(UnprocessableRequestError):
    """A doc was written without a topic, and nobody could say which it belongs under."""

    code = "topic_unclear"


@dataclass(frozen=True)
class Written:
    doc: Doc
    created: bool
    filed_by: Literal["caller", "jev", "only_topic"]
    confidence: float | None


async def write(
    session: AsyncSession,
    project: Project,
    data: DocWrite,
    judge: DocJudge | None,
    *,
    confidence: float,
    author_id: UUID | None,
) -> Written:
    """File a doc on the project, choosing its topic when the writer did not.

    The topic is the writer's when named. Otherwise it is the project's only
    one, or jev's pick — but only a confident pick. Below ``confidence``, or
    when jev cannot be asked, the write is refused with every topic to choose
    from, ranked when jev had an opinion: a doc filed in the wrong place is a
    doc nobody finds, and naming the topic costs the writer one more call.

    Topics are never made here. They are the headings people chose, and a
    writer that could add one whenever nothing fitted would grow them without
    limit.

    Raises:
        UnprocessableRequestError: if the project has no topics, or
            ``topic_id`` is not one of them, or an append would outgrow a doc.
        TopicUnclearError: when the topic could not be chosen for the writer.
        ConflictError: if the topic already holds a doc of that title and
            ``append`` is not set.
    """
    topics = await topics_for(session, project.id)
    if not topics:
        raise UnprocessableRequestError(
            f"{project.key} has no doc topics yet, and a doc has to be filed under one. "
            "Topics are made by people, on the project's Docs page.",
            details={"topics": []},
        )

    filed_by: Literal["caller", "jev", "only_topic"]
    certainty: float | None = None
    if data.topic_id is not None:
        topic = next((topic for topic in topics if topic.id == data.topic_id), None)
        if topic is None:
            raise UnprocessableRequestError(
                "That topic is not on this project.",
                details={"topic_id": str(data.topic_id), "topics": _topic_choices(topics)},
            )
        filed_by = "caller"
    elif len(topics) == 1:
        topic, filed_by = topics[0], "only_topic"
    else:
        topic, certainty = await _file(session, project, data, topics, judge, confidence)
        filed_by = "jev"

    existing = next(
        (
            doc
            for doc in await _topic_docs(session, topic.id)
            if doc.title.casefold() == data.title.casefold()
        ),
        None,
    )
    if existing is None:
        doc = await create_doc(
            session, topic, DocCreate(title=data.title, body=data.body), author_id=author_id
        )
        return Written(doc=doc, created=True, filed_by=filed_by, confidence=certainty)

    if not data.append:
        raise ConflictError(
            f"{topic.name!r} already has a doc called {existing.title!r}. "
            "Set append to add to it, or edit it by its id.",
            details={"doc_id": str(existing.id), "topic_id": str(topic.id)},
        )
    combined = appended(existing.body, data.body)
    if len(combined) > BODY_MAX_LENGTH:
        raise UnprocessableRequestError(
            f"{existing.title!r} would be longer than {BODY_MAX_LENGTH} characters.",
            details={"doc_id": str(existing.id)},
        )
    existing.body = combined
    await session.flush()
    await session.refresh(existing, ["author", "updated_at"])
    return Written(doc=existing, created=False, filed_by=filed_by, confidence=certainty)


async def _file(
    session: AsyncSession,
    project: Project,
    data: DocWrite,
    topics: list[DocTopic],
    judge: DocJudge | None,
    confidence: float,
) -> tuple[DocTopic, float]:
    """jev's topic for a doc, when it is sure enough; otherwise the refusal."""
    if judge is None:
        raise _unclear(topics, "jev is not configured on this server, so name a topic.")

    titles: dict[UUID, list[str]] = {topic.id: [] for topic in topics}
    for doc in await docs_for(session, project.id):
        titles[doc.topic_id].append(doc.title)
    offered = [
        TopicOnFile(
            id=topic.id,
            section_label=topic.section.label,
            name=topic.name,
            doc_titles=titles[topic.id],
        )
        for topic in topics
    ]
    try:
        filing = await judge.filing(data.title, data.body, offered)
    except JudgeUnavailableError as exc:
        raise _unclear(topics, f"jev could not be asked ({exc}), so name a topic.") from exc

    if filing.confidence < confidence:
        raise _unclear(
            topics,
            f"jev is not sure where this belongs (confidence {filing.confidence:.2f}), "
            "so name a topic. Its ranking is attached.",
            ranking=dict(filing.ranking),
        )
    by_id = {topic.id: topic for topic in topics}
    return by_id[filing.topic_id], filing.confidence


def _unclear(
    topics: list[DocTopic], message: str, *, ranking: dict[UUID, float] | None = None
) -> TopicUnclearError:
    """The refusal, listing every topic — most likely first when jev ranked them."""
    if ranking is not None:
        topics = sorted(topics, key=lambda topic: -ranking.get(topic.id, 0.0))
    choices = _topic_choices(topics)
    if ranking is not None:
        for topic, entry in zip(topics, choices, strict=True):
            entry["probability"] = ranking.get(topic.id)
    return TopicUnclearError(message, details={"topics": choices})


def _topic_choices(topics: list[DocTopic]) -> list[dict[str, object]]:
    return [
        {"id": str(topic.id), "section": topic.section.label, "name": topic.name}
        for topic in topics
    ]


_LIST_ITEM = ("- ", "* ", "+ ")


def appended(body: str, addition: str) -> str:
    """``addition`` put on the end of ``body``.

    A list item follows a list item on the next line, so a `learned.md` stays
    one list; anything else starts a paragraph of its own.
    """
    before, after = body.rstrip(), addition.strip("\n")
    if not before:
        return after
    joins_a_list = after.lstrip().startswith(_LIST_ITEM) and (
        before.splitlines()[-1].lstrip().startswith(_LIST_ITEM)
    )
    return f"{before}\n{after}" if joins_a_list else f"{before}\n\n{after}"


# --- Helpers -----------------------------------------------------------------


def _compact(rows: Sequence[DocTopic | Doc]) -> None:
    for position, row in enumerate(rows):
        row.position = position


def _refuse_partial_order(current: list[UUID], asked: Sequence[UUID], noun: str) -> None:
    if len(asked) != len(current) or set(asked) != set(current):
        raise UnprocessableRequestError(
            f"Name every {noun} here exactly once, in the order they should be drawn.",
            details={"expected": [str(item) for item in current]},
        )
