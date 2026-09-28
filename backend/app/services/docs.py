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
from typing import Literal, cast
from uuid import UUID

from jevdocs import Action, Change, Route
from jevdocs.placement import (
    ADD_RELATED,
    LINK,
    NEW_FILE,
    NEW_GROUP,
    ORDER,
    SPLIT_GROUP,
    UPDATE_GROUP_README,
)
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.models.doc import SECTION_ORDER, Doc, DocSection, DocTopic
from app.models.project import Project
from app.schemas.docs import (
    BODY_MAX_LENGTH,
    AnswerStatus,
    DocCreate,
    DocPlace,
    DocTopicCreate,
    DocUpdate,
    DocWrite,
)
from app.services import doc_corpus
from app.services.doc_corpus import FiledDoc, FiledTopic, ProjectCorpus
from app.services.doc_engine import DocEngine, EngineUnavailableError

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


async def corpus_for(session: AsyncSession, project: Project) -> ProjectCorpus:
    """The project's docs as jev-docs reads them, bodies and all, in tree order."""
    topics = await topics_for(session, project.id)
    by_topic: dict[UUID, list[Doc]] = {topic.id: [] for topic in topics}
    for doc in await session.scalars(
        select(Doc).where(Doc.project_id == project.id).order_by(Doc.position, Doc.created_at)
    ):
        by_topic[doc.topic_id].append(doc)
    return doc_corpus.build(project.key, [(topic, by_topic[topic.id]) for topic in topics])


@dataclass(frozen=True)
class Found:
    doc: FiledDoc
    section: str | None
    text: str
    whole_doc: bool
    relevance: float | None


@dataclass(frozen=True)
class Weighed:
    doc: FiledDoc
    section: str | None
    score: float
    relevance: float | None


@dataclass(frozen=True)
class Answer:
    status: AnswerStatus
    found: Found | None
    also: Found | None
    alternatives: list[Weighed]
    reason: str | None


async def ask(
    session: AsyncSession, project: Project, question: str, engine: DocEngine | None
) -> Answer:
    """The section of the project's docs that answers ``question``.

    jev-docs routes it — section, topic, doc, section — and then reads the
    section it chose against the question; only a section that passes that
    check is ``ok``. Anything short of it tells the agent to find the answer in
    the code, and so does jev being unreachable: ``unavailable`` is a miss, not
    an error, because an agent that stopped whenever a model was down would
    stop for nothing.
    """
    filed = await corpus_for(session, project)
    if filed.empty:
        return Answer("not_documented", None, None, [], f"{project.key} has no docs yet.")
    if engine is None:
        return Answer("unavailable", None, None, [], "jev is not configured on this server.")
    try:
        route = await engine.ask(filed.corpus, question)
    except EngineUnavailableError as exc:
        logger.warning(
            "jev could not route a question; the agent will read the code",
            extra={"context": {"project": project.key, "error": str(exc)}},
        )
        return Answer("unavailable", None, None, [], f"jev could not be asked: {exc}")

    found = _found(filed, route)
    alternatives = [
        Weighed(
            doc=doc,
            section=candidate.section_obj.title if candidate.section_obj else None,
            score=candidate.score,
            relevance=candidate.relevance,
        )
        for candidate in route.checked
        if candidate.ref != route.ref and (doc := filed.doc_at(candidate.ref)) is not None
    ]
    return Answer(
        status=cast(AnswerStatus, route.status),
        found=found,
        also=_found(filed, route.also) if route.also is not None else None,
        alternatives=alternatives,
        reason="; ".join(route.notes) or None,
    )


def _found(filed: ProjectCorpus, route: Route) -> Found | None:
    doc = filed.doc_at(route.ref) if route.ref else None
    if doc is None:
        return None
    section = None if route.whole_file else route.section_obj
    return Found(
        doc=doc,
        section=section.title if section is not None else None,
        text=route.text,
        whole_doc=section is None,
        relevance=route.relevance,
    )


# --- Where a new fact goes -------------------------------------------------------


@dataclass(frozen=True)
class Edit:
    kind: str
    sure: bool
    probability: float
    why: str
    facts: list[int]
    doc: FiledDoc | None = None
    topic: FiledTopic | None = None
    section: str | None = None
    related: FiledDoc | None = None


@dataclass(frozen=True)
class Placed:
    status: Literal["planned", "unavailable"]
    reason: str | None
    edits: list[Edit]


_HANDLED_FOR_YOU = {UPDATE_GROUP_README, SPLIT_GROUP}
"""Plan actions Cylist's docs have no use for: a topic's description is made
from the docs filed under it, and a topic holds as many docs as it is given.
The one exception is an unsure README update, which is the Placer's fallback
home for a new doc — see :func:`_edit`."""


async def place(
    session: AsyncSession, project: Project, data: DocPlace, engine: DocEngine | None
) -> Placed:
    """Where each fact in ``data`` belongs in the project's docs. Writes nothing.

    jev-docs plans it: for every fact, which docs it touches and which section
    of each should hold it — or a doc of its own when none accepts it — and
    which sections the facts leave out of date. The agent makes the edits with
    ``write_doc``; a plan it can read is worth more than an edit it cannot.

    Raises:
        UnprocessableRequestError: if the project has no topics to file under.
    """
    filed = await corpus_for(session, project)
    if not filed.topics:
        raise UnprocessableRequestError(
            f"{project.key} has no doc topics yet, and a doc has to be filed under one. "
            "Topics are made by people, on the project's Docs page.",
            details={"topics": []},
        )
    if engine is None:
        return Placed("unavailable", "jev is not configured on this server.", [])

    change = Change.from_dict(
        {
            "title": data.title,
            "summary": data.summary,
            "entities": data.entities,
            "facts": [
                {"id": f"f{index}", "text": fact.text, "shape": fact.shape or ""}
                for index, fact in enumerate(data.facts)
            ],
        }
    )
    try:
        plan = await engine.place(filed.corpus, change)
    except EngineUnavailableError as exc:
        logger.warning(
            "jev could not place a change; the agent will choose where it goes",
            extra={"context": {"project": project.key, "error": str(exc)}},
        )
        return Placed("unavailable", f"jev could not be asked: {exc}", [])

    ordered = sorted(
        plan.actions, key=lambda action: (ORDER.index(action.kind), -action.probability)
    )
    edits = [edit for action in ordered if (edit := _edit(filed, action)) is not None]
    return Placed("planned", None, edits)


def _edit(filed: ProjectCorpus, action: Action) -> Edit | None:
    """One plan action, addressed the way Cylist addresses docs and topics."""
    facts = [int(fact.removeprefix("f")) for fact in action.facts]

    def edit(kind: str, **where: object) -> Edit:
        return Edit(
            kind=kind,
            sure=action.sure,
            probability=action.probability,
            why=action.why,
            facts=facts,
            **where,  # type: ignore[arg-type]
        )

    if action.kind == UPDATE_GROUP_README:
        topic = filed.topic_at(action.ref)
        if action.sure or topic is None:
            return None
        return edit(NEW_FILE, topic=topic)
    if action.kind in _HANDLED_FOR_YOU:
        return None
    if action.kind == NEW_GROUP:
        return edit("new_topic")
    if action.kind == NEW_FILE:
        return edit(NEW_FILE, topic=filed.topic_at(action.ref))
    if action.kind == ADD_RELATED:
        source, _, target = action.ref.partition(LINK)
        doc = filed.doc_at(source)
        related = filed.doc_at(target) if target else None
        return edit(ADD_RELATED, doc=doc, related=related) if doc is not None else None

    doc = filed.doc_at(action.ref)
    if doc is None:
        return None
    _, _, anchor = action.ref.partition("#")
    return edit(action.kind, doc=doc, section=doc.section_title(anchor) if anchor else None)


# --- What agents write -----------------------------------------------------------


class TopicUnclearError(UnprocessableRequestError):
    """A doc was written without a topic, on a project with more than one."""

    code = "topic_unclear"


@dataclass(frozen=True)
class Written:
    doc: Doc
    created: bool
    filed_by: Literal["caller", "only_topic"]


async def write(
    session: AsyncSession,
    project: Project,
    data: DocWrite,
    *,
    author_id: UUID | None,
) -> Written:
    """File a doc on the project under the topic its writer named.

    The topic may be left out only when the project has just one. Otherwise
    the write is refused with every topic to choose from: a doc filed in the
    wrong place is a doc nobody finds, and :func:`place` is how a writer finds
    the right one.

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

    filed_by: Literal["caller", "only_topic"]
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
        raise TopicUnclearError(
            "Name the topic to file this under — `place` says which. "
            "The project's topics are attached.",
            details={"topics": _topic_choices(topics)},
        )

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
        return Written(doc=doc, created=True, filed_by=filed_by)

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
    return Written(doc=existing, created=False, filed_by=filed_by)


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
