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

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import defer

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.models.doc import SECTION_ORDER, Doc, DocSection, DocTopic
from app.models.project import Project
from app.schemas.docs import DocCreate, DocTopicCreate, DocUpdate


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
