"""Asking a project's docs a question: the one section that answers it, if any."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import cast

from jevdocs import Route

from app.models.project import Project
from app.schemas.docs import AnswerStatus
from app.services.doc_corpus import FiledDoc, ProjectCorpus
from app.services.doc_engine import DocEngine, EngineUnavailableError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Found:
    """A section jev-docs routed a question to, addressed the way Cylist addresses docs."""

    doc: FiledDoc
    section: str | None
    text: str
    whole_doc: bool
    relevance: float | None


@dataclass(frozen=True)
class Weighed:
    """A section jev-docs considered on the way and did not choose."""

    doc: FiledDoc
    section: str | None
    score: float
    relevance: float | None


@dataclass(frozen=True)
class Answer:
    """What the docs say to a question, and how sure they are of it."""

    status: AnswerStatus
    found: Found | None
    also: Found | None
    alternatives: list[Weighed]
    reason: str | None


def _miss(status: AnswerStatus, reason: str) -> Answer:
    """An answer with nothing in it, saying why."""
    return Answer(status, None, None, [], reason)


async def ask(
    filed: ProjectCorpus, project: Project, question: str, engine: DocEngine | None
) -> Answer:
    """The section of the project's docs that answers ``question``.

    jev-docs routes it — section, topic, doc, section — and then reads the
    section it chose against the question; only a section that passes that
    check is ``ok``. Anything short of it tells the agent to find the answer in
    the code, and so does jev being unreachable: ``unavailable`` is a miss, not
    an error, because an agent that stopped whenever a model was down would
    stop for nothing.

    Takes the corpus rather than a session, because nothing here may hold a
    database connection: jev can take tens of seconds to answer, and a
    connection waiting on it is one the rest of the server cannot have.
    """
    if filed.empty:
        return _miss("not_documented", f"{project.key} has no docs yet.")
    if engine is None:
        return _miss("unavailable", "jev is not configured on this server.")
    try:
        route = await engine.ask(filed.corpus, question)
    except EngineUnavailableError as exc:
        logger.warning(
            "jev could not route a question; the agent will read the code",
            extra={"context": {"project": project.key, "error": str(exc)}},
        )
        return _miss("unavailable", f"jev could not be asked: {exc}")

    return Answer(
        status=cast(AnswerStatus, route.status),
        found=_found(filed, route),
        also=_found(filed, route.also) if route.also is not None else None,
        alternatives=_alternatives(filed, route),
        reason="; ".join(route.notes) or None,
    )


def _found(filed: ProjectCorpus, route: Route) -> Found | None:
    """The doc and section a route lands on, or None if it names no doc here."""
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


def _alternatives(filed: ProjectCorpus, route: Route) -> list[Weighed]:
    """Every other section the route checked, so an agent can see what nearly answered."""
    alternatives: list[Weighed] = []
    for candidate in route.checked:
        if candidate.ref == route.ref:
            continue
        doc = filed.doc_at(candidate.ref)
        if doc is None:
            continue
        alternatives.append(
            Weighed(
                doc=doc,
                section=candidate.section_obj.title if candidate.section_obj else None,
                score=candidate.score,
                relevance=candidate.relevance,
            )
        )
    return alternatives
