"""What agents write into a project's docs: a new doc, or a line on an old one."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, Reason, UnprocessableRequestError
from app.models.doc import Doc, DocTopic
from app.models.project import Project
from app.services.commands.docs import BODY_MAX_LENGTH, DocCreate, DocWrite
from app.services.docs.filing import create_doc, no_topics_error, topic_docs, topics_for

FiledBy = Literal["caller", "only_topic"]
"""How the topic a doc went under was chosen: named by its writer, or the
project's only one."""

_LIST_ITEM = ("- ", "* ", "+ ")


class TopicUnclearError(UnprocessableRequestError):
    """A doc was written without a topic, on a project with more than one."""

    code = "topic_unclear"


@dataclass(frozen=True)
class Written:
    """A doc as it stands after a write, and how it got there."""

    doc: Doc
    created: bool
    filed_by: FiledBy


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
    wrong place is a doc nobody finds, and
    :func:`app.services.docs.placing.place` is how a writer finds the right one.

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
    topic, filed_by = await _topic_to_file_under(session, project, data.topic_id)

    existing = next(
        (
            doc
            for doc in await topic_docs(session, topic.id)
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
            reason=Reason.DUPLICATE_NAME,
        )
    # Read now and not with the titles: only the doc being appended to needs
    # its body, and the topic's others may be long.
    await session.refresh(existing, ["body"])
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


async def _topic_to_file_under(
    session: AsyncSession, project: Project, topic_id: UUID | None
) -> tuple[DocTopic, FiledBy]:
    """The topic a write goes under, and how it was chosen.

    Raises:
        UnprocessableRequestError: if the project has no topics, or
            ``topic_id`` is not one of them.
        TopicUnclearError: if none was named and the project has several.
    """
    topics = await topics_for(session, project.id)
    if not topics:
        raise no_topics_error(project)

    if topic_id is not None:
        named = next((topic for topic in topics if topic.id == topic_id), None)
        if named is None:
            raise UnprocessableRequestError(
                "That topic is not on this project.",
                details={"topic_id": str(topic_id), "topics": _topic_choices(topics)},
            )
        return named, "caller"

    if len(topics) == 1:
        return topics[0], "only_topic"

    raise TopicUnclearError(
        "Name the topic to file this under — `place` says which. "
        "The project's topics are attached.",
        details={"topics": _topic_choices(topics)},
    )


def _topic_choices(topics: list[DocTopic]) -> list[dict[str, object]]:
    return [
        {"id": str(topic.id), "section": topic.section.label, "name": topic.name}
        for topic in topics
    ]


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
