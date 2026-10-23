"""Where a new fact goes in a project's docs — a plan, never a write."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

from jevdocs import Action, Change
from jevdocs.placement import (
    ADD_RELATED,
    LINK,
    NEW_FILE,
    NEW_GROUP,
    ORDER,
    SPLIT_GROUP,
    UPDATE_GROUP_README,
)

from app.models.project import Project
from app.services.commands.docs import DocPlace
from app.services.doc_corpus import FiledDoc, FiledTopic, ProjectCorpus
from app.services.doc_engine import DocEngine, EngineUnavailableError
from app.services.docs.filing import no_topics_error

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Edit:
    """One edit the plan asks for, addressed by Cylist's docs and topics."""

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
    """The whole plan, or why there is none."""

    status: Literal["planned", "unavailable"]
    reason: str | None
    edits: list[Edit]


_HANDLED_FOR_YOU = {UPDATE_GROUP_README, SPLIT_GROUP}
"""Plan actions Cylist's docs have no use for: a topic's description is made
from the docs filed under it, and a topic holds as many docs as it is given.
The one exception is an unsure README update, which is the Placer's fallback
home for a new doc — see :func:`_edit`."""


async def place(
    filed: ProjectCorpus, project: Project, data: DocPlace, engine: DocEngine | None
) -> Placed:
    """Where each fact in ``data`` belongs in the project's docs. Writes nothing.

    jev-docs plans it: for every fact, which docs it touches and which section
    of each should hold it — or a doc of its own when none accepts it — and
    which sections the facts leave out of date. The agent makes the edits with
    ``write_doc``; a plan it can read is worth more than an edit it cannot.
    Given the corpus, not a session, for :func:`~.asking.ask`'s reason.

    Raises:
        UnprocessableRequestError: if the project has no topics to file under.
    """
    if not filed.topics:
        raise no_topics_error(project)
    if engine is None:
        return Placed("unavailable", "jev is not configured on this server.", [])

    try:
        plan = await engine.place(filed.corpus, _change_of(data))
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


def _change_of(data: DocPlace) -> Change:
    """The request as jev-docs takes it, each fact named ``f<index>`` so the
    plan's references to them can be turned back into indexes."""
    return Change.from_dict(
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


def _edit(filed: ProjectCorpus, action: Action) -> Edit | None:
    """One plan action, addressed the way Cylist addresses docs and topics."""
    facts = [int(fact.removeprefix("f")) for fact in action.facts]

    def edit(
        kind: str,
        *,
        doc: FiledDoc | None = None,
        topic: FiledTopic | None = None,
        section: str | None = None,
        related: FiledDoc | None = None,
    ) -> Edit:
        return Edit(
            kind=kind,
            sure=action.sure,
            probability=action.probability,
            why=action.why,
            facts=facts,
            doc=doc,
            topic=topic,
            section=section,
            related=related,
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
