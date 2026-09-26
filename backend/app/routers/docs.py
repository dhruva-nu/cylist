"""Docs: a project's markdown, filed as section → topic → doc.

The tree is one request — `GET /projects/{ref}/docs` — and carries no bodies;
a doc's markdown is read with `GET /docs/{id}`. Topics and docs are addressed
by id: neither has a reference of its own, because neither is something people
read out to each other the way they do `ATL-41`.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.doc import SECTION_ORDER, Doc, DocTopic
from app.models.project import Project
from app.routers import guards
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.docs import (
    DocCreate,
    DocListing,
    DocOrder,
    DocRead,
    DocSectionTree,
    DocTopicCreate,
    DocTopicOrder,
    DocTopicRead,
    DocTopicUpdate,
    DocTopicWithDocs,
    DocTree,
    DocUpdate,
)
from app.schemas.people import PersonRead
from app.services import activity, docs

router = APIRouter(tags=["docs"])


async def resolved_topic(
    topic_id: UUID = Path(description="The topic's id."),
    session: AsyncSession = SessionDependency,
) -> DocTopic:
    return await docs.get_topic(session, topic_id)


async def resolved_doc(
    doc_id: UUID = Path(description="The doc's id."),
    session: AsyncSession = SessionDependency,
) -> Doc:
    return await docs.get_doc(session, doc_id)


WRITE_DOCS = guards.on_project(Permission.DOCS)
WRITE_THIS_TOPIC = guards.for_entity(Permission.DOCS, resolved_topic)
WRITE_THIS_DOC = guards.for_entity(Permission.DOCS, resolved_doc)


def _listing(doc: Doc) -> DocListing:
    return DocListing(
        id=doc.id,
        topic_id=doc.topic_id,
        title=doc.title,
        position=doc.position,
        author=PersonRead.model_validate(doc.author) if doc.author else None,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


async def _doc_read(session: AsyncSession, doc: Doc) -> DocRead:
    topic = await docs.get_topic(session, doc.topic_id)
    return DocRead(
        **_listing(doc).model_dump(),
        project_id=doc.project_id,
        section=topic.section,
        topic_name=topic.name,
        body=doc.body,
    )


def _topic_read(topic: DocTopic, doc_count: int) -> DocTopicRead:
    return DocTopicRead(
        id=topic.id,
        project_id=topic.project_id,
        section=topic.section,
        name=topic.name,
        position=topic.position,
        doc_count=doc_count,
    )


async def _tree(session: AsyncSession, project: Project) -> DocTree:
    topics = await docs.topics_for(session, project.id)
    filed: dict[UUID, list[Doc]] = defaultdict(list)
    found = await docs.docs_for(session, project.id)
    for doc in found:
        filed[doc.topic_id].append(doc)
    return DocTree(
        sections=[
            DocSectionTree(
                section=section,
                label=section.label,
                topics=[
                    DocTopicWithDocs(
                        **_topic_read(topic, len(filed[topic.id])).model_dump(),
                        docs=[_listing(doc) for doc in filed[topic.id]],
                    )
                    for topic in topics
                    if topic.section is section
                ],
            )
            for section in SECTION_ORDER
        ],
        doc_count=len(found),
    )


async def _record(
    session: AsyncSession,
    principal: Principal,
    verb: str,
    *,
    entity_type: str,
    entity_id: UUID | None,
    project_id: UUID,
    **payload: object,
) -> None:
    await activity.record(
        session,
        principal,
        verb,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        payload=payload,
    )


# --- The tree -----------------------------------------------------------------


@router.get(
    "/projects/{project_ref}/docs",
    response_model=DocTree,
    summary="Get a project's docs, filed by section and topic",
)
async def get_tree(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> DocTree:
    """Both sections — Product, then Engineering — each with its topics in
    order and each topic with its docs in order. Bodies are left out."""
    return await _tree(session, project)


# --- Topics -------------------------------------------------------------------


@router.post(
    "/projects/{project_ref}/doc-topics",
    response_model=DocTopicRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a doc topic",
    responses={409: {"description": "That section already has a topic by that name."}},
)
async def create_topic(
    body: DocTopicCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_DOCS),
    session: AsyncSession = SessionDependency,
) -> DocTopicRead:
    """Add a topic at the bottom of a section. Topics are one level deep."""
    topic = await docs.create_topic(session, project, body)
    await _record(
        session,
        principal,
        "doc_topic.created",
        entity_type="doc_topic",
        entity_id=topic.id,
        project_id=project.id,
        section=topic.section.value,
        name=topic.name,
    )
    return _topic_read(topic, 0)


@router.put(
    "/projects/{project_ref}/doc-topics/order",
    response_model=list[DocTopicRead],
    summary="Reorder a section's topics",
    responses={422: {"description": "The list does not name every topic in the section once."}},
)
async def reorder_topics(
    body: DocTopicOrder,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_DOCS),
    session: AsyncSession = SessionDependency,
) -> list[DocTopicRead]:
    """Put a section's topics in exactly the order given, top first."""
    ordered = await docs.reorder_topics(session, project, body.section, body.topic_ids)
    await _record(
        session,
        principal,
        "doc_topic.reordered",
        entity_type="doc_topic",
        entity_id=None,
        project_id=project.id,
        section=body.section.value,
    )
    counts = await docs.counts_by_topic(session, project.id)
    return [_topic_read(topic, counts.get(topic.id, 0)) for topic in ordered]


@router.patch(
    "/doc-topics/{topic_id}",
    response_model=DocTopicRead,
    summary="Rename a doc topic",
    responses={409: {"description": "That section already has a topic by that name."}},
)
async def rename_topic(
    body: DocTopicUpdate,
    topic: DocTopic = Depends(resolved_topic),
    principal: Principal = Depends(WRITE_THIS_TOPIC),
    session: AsyncSession = SessionDependency,
) -> DocTopicRead:
    """Rename a topic. Its section is fixed — its docs were filed under it."""
    before = topic.name
    renamed = await docs.rename_topic(session, topic, body.name)
    await _record(
        session,
        principal,
        "doc_topic.renamed",
        entity_type="doc_topic",
        entity_id=renamed.id,
        project_id=renamed.project_id,
        before=before,
        name=renamed.name,
    )
    counts = await docs.counts_by_topic(session, renamed.project_id)
    return _topic_read(renamed, counts.get(renamed.id, 0))


@router.delete(
    "/doc-topics/{topic_id}",
    response_model=Acknowledged,
    summary="Delete an empty doc topic",
    responses={409: {"description": "Docs are still filed under this topic."}},
)
async def delete_topic(
    topic: DocTopic = Depends(resolved_topic),
    principal: Principal = Depends(WRITE_THIS_TOPIC),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a topic with nothing in it.

    Refused while any doc is filed under it, and the refusal says how many: a
    topic is a heading, and the docs under it are not worth losing over one.
    """
    project_id, name, section = topic.project_id, topic.name, topic.section.value
    await docs.delete_topic(session, topic)
    await _record(
        session,
        principal,
        "doc_topic.deleted",
        entity_type="doc_topic",
        entity_id=None,
        project_id=project_id,
        section=section,
        name=name,
    )
    return Acknowledged()


@router.put(
    "/doc-topics/{topic_id}/order",
    response_model=list[DocListing],
    summary="Reorder a topic's docs",
    responses={422: {"description": "The list does not name every doc in the topic once."}},
)
async def reorder_docs(
    body: DocOrder,
    topic: DocTopic = Depends(resolved_topic),
    principal: Principal = Depends(WRITE_THIS_TOPIC),
    session: AsyncSession = SessionDependency,
) -> list[DocListing]:
    """Put a topic's docs in exactly the order given, top first."""
    ordered = await docs.reorder_docs(session, topic, body.doc_ids)
    await _record(
        session,
        principal,
        "doc.reordered",
        entity_type="doc_topic",
        entity_id=topic.id,
        project_id=topic.project_id,
        name=topic.name,
    )
    return [_listing(doc) for doc in ordered]


# --- Docs ---------------------------------------------------------------------


@router.post(
    "/doc-topics/{topic_id}/docs",
    response_model=DocRead,
    status_code=status.HTTP_201_CREATED,
    summary="Write a doc",
)
async def create_doc(
    body: DocCreate,
    topic: DocTopic = Depends(resolved_topic),
    principal: Principal = Depends(WRITE_THIS_TOPIC),
    session: AsyncSession = SessionDependency,
) -> DocRead:
    """File a new markdown doc at the bottom of a topic. Whoever sends it is
    its author."""
    doc = await docs.create_doc(session, topic, body, author_id=principal.person_id)
    await _record(
        session,
        principal,
        "doc.created",
        entity_type="doc",
        entity_id=doc.id,
        project_id=doc.project_id,
        title=doc.title,
        topic=topic.name,
    )
    return await _doc_read(session, doc)


@router.get("/docs/{doc_id}", response_model=DocRead, summary="Read a doc")
async def get_doc(
    doc: Doc = Depends(resolved_doc),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> DocRead:
    """One doc with its markdown, and the section and topic it is filed under."""
    return await _doc_read(session, doc)


@router.patch(
    "/docs/{doc_id}",
    response_model=DocRead,
    summary="Edit or move a doc",
    responses={422: {"description": "`topic_id` is not a topic on this doc's project."}},
)
async def update_doc(
    body: DocUpdate,
    doc: Doc = Depends(resolved_doc),
    principal: Principal = Depends(WRITE_THIS_DOC),
    session: AsyncSession = SessionDependency,
) -> DocRead:
    """Change any of a doc's title and body, or move it.

    Setting `topic_id` moves it to another topic on the same project — either
    section — at `position`, or at the bottom when that is left out. `position`
    alone moves it within its own topic.
    """
    origin = doc.topic_id
    updated = await docs.update_doc(session, doc, body)
    await _record(
        session,
        principal,
        "doc.moved" if updated.topic_id != origin else "doc.updated",
        entity_type="doc",
        entity_id=updated.id,
        project_id=updated.project_id,
        title=updated.title,
        fields=sorted(body.model_dump(exclude_unset=True)),
    )
    return await _doc_read(session, updated)


@router.delete("/docs/{doc_id}", response_model=Acknowledged, summary="Delete a doc")
async def delete_doc(
    doc: Doc = Depends(resolved_doc),
    principal: Principal = Depends(WRITE_THIS_DOC),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    project_id, title = doc.project_id, doc.title
    await docs.delete_doc(session, doc)
    await _record(
        session,
        principal,
        "doc.deleted",
        entity_type="doc",
        entity_id=None,
        project_id=project_id,
        title=title,
    )
    return Acknowledged()
