"""Asking jev about a project's docs: which a card needs, and where a new one goes.

jev is TypeSafe's System One model (https://api.typesafe.ai). It answers typed
questions about a piece of content with calibrated probabilities rather than
generated text, which is the shape both questions here want:

* **Relevance** is one request per card. The card — title, description,
  checklist — is the state, and every doc on the project is one yes/no
  (Noul) question: "does work on this card need <doc>?". What comes back is a
  probability per doc, so the caller can rank them and cut at a threshold.
* **Filing** is one request per doc. The doc is the state, and a single Choice
  over the project's topics says which it belongs under, with a confidence
  that says whether to file it there or ask.

Everything else — what to do with the numbers, and what to do when there are
none — belongs to :mod:`app.services.docs`. Behind a :class:`DocJudge` so the
tests can stand a fake in for the network, and so a deployment with no key
configured has no judge at all rather than one that always fails.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID

from jev import AsyncJev, ChoiceAnswer, JevError, NoulAnswer, choice, noul

from app.config import Settings

SUMMARY_MAX_CHARS = 240
"""How much of a doc stands in for it in a relevance question. Enough for its
opening sentence or two; a doc's title and topic say the rest."""

FILING_BODY_MAX_CHARS = 4000
"""How much of a new doc jev reads to file it. The opening of a doc says what
it is about; the rest only costs tokens."""


class JudgeUnavailableError(Exception):
    """jev could not be asked, or did not answer in a usable shape."""


@dataclass(frozen=True)
class Card:
    """A task as jev reads it."""

    reference: str
    title: str
    description: str
    checklist: Sequence[str]

    def as_state(self) -> dict[str, Any]:
        return {
            "card": {
                "reference": self.reference,
                "title": self.title,
                "description": self.description,
                "checklist": list(self.checklist),
            }
        }


@dataclass(frozen=True)
class DocOnFile:
    """One doc, as a relevance question names it."""

    id: UUID
    title: str
    section_label: str
    topic_name: str
    summary: str


@dataclass(frozen=True)
class TopicOnFile:
    """One topic, as a filing question offers it."""

    id: UUID
    section_label: str
    name: str
    doc_titles: Sequence[str]

    @property
    def label(self) -> str:
        return topic_label(self.section_label, self.name)


@dataclass(frozen=True)
class Filing:
    """Where jev would file a doc, and how sure it is."""

    topic_id: UUID
    confidence: float
    ranking: Sequence[tuple[UUID, float]]
    """Every topic with jev's probability for it, most likely first."""


class DocJudge(Protocol):
    async def relevance(self, card: Card, docs: Sequence[DocOnFile]) -> dict[UUID, float]:
        """The probability that work on ``card`` needs each doc, by doc id."""
        ...

    async def filing(self, title: str, body: str, topics: Sequence[TopicOnFile]) -> Filing:
        """Which of ``topics`` a doc belongs under. Needs at least two."""
        ...


class JevJudge:
    """A :class:`DocJudge` that asks jev."""

    def __init__(self, api_key: str, *, model: str, timeout: float) -> None:
        self._api_key = api_key
        self._model = model
        self._timeout = timeout

    def _client(self) -> AsyncJev:
        # One retry, not the SDK's two: a relevance check stands between an
        # agent and its card, and the whole tree is a better answer than a
        # third wait.
        return AsyncJev(self._api_key, model=self._model, timeout=self._timeout, max_retries=1)

    async def relevance(self, card: Card, docs: Sequence[DocOnFile]) -> dict[UUID, float]:
        questions = [relevance_question(index, doc) for index, doc in enumerate(docs)]
        try:
            async with self._client() as jev:
                result = await jev.ask(card.as_state(), *questions)
        except JevError as exc:
            raise JudgeUnavailableError(str(exc)) from exc
        probabilities: dict[UUID, float] = {}
        for doc, question in zip(docs, questions, strict=True):
            answer = result[question]
            if not isinstance(answer, NoulAnswer):
                raise JudgeUnavailableError(f"jev answered {question.name} as {answer!r}")
            probabilities[doc.id] = answer.probability
        return probabilities

    async def filing(self, title: str, body: str, topics: Sequence[TopicOnFile]) -> Filing:
        question = filing_question(topics)
        try:
            async with self._client() as jev:
                result = await jev.ask(filing_state(title, body), question)
        except JevError as exc:
            raise JudgeUnavailableError(str(exc)) from exc
        answer = result[question]
        if not isinstance(answer, ChoiceAnswer):
            raise JudgeUnavailableError(f"jev answered the topic as {answer!r}")
        by_label = {topic.label: topic.id for topic in topics}
        return Filing(
            topic_id=by_label[answer.choice],
            confidence=answer.confidence,
            ranking=[(by_label[label], probability) for label, probability in answer.ranked()],
        )


def judge_for(settings: Settings) -> DocJudge | None:
    """The judge this deployment is configured for, or None without a key."""
    if not settings.jev_api_key:
        return None
    return JevJudge(
        settings.jev_api_key, model=settings.jev_model, timeout=settings.jev_timeout_seconds
    )


# --- The questions ------------------------------------------------------------


def relevance_question(index: int, doc: DocOnFile) -> Any:
    """One yes/no per doc, named by its position: does work on this card need it?"""
    about = f": {doc.summary}" if doc.summary else ""
    return noul(
        f"doc_{index}",
        f"Does work on this card need the doc {doc.title!r}, filed under "
        f"{doc.section_label} → {doc.topic_name}{about}?",
        true_means="someone doing this card would want to read this doc first",
        false_means="the doc is about something this card does not touch",
    )


def filing_question(topics: Sequence[TopicOnFile]) -> Any:
    """One Choice over the project's topics, each described by what it holds."""
    return choice(
        "topic",
        "Which topic of this project's docs should this new doc be filed under?",
        {topic.label: _holds(topic.doc_titles) for topic in topics},
    )


def filing_state(title: str, body: str) -> dict[str, Any]:
    return {"doc": {"title": title, "body": body[:FILING_BODY_MAX_CHARS]}}


def topic_label(section_label: str, name: str) -> str:
    """A topic as jev sees it, ``Engineering / DB schema``: unique, since a name
    is unique within its section."""
    return f"{section_label} / {name}"


def _holds(doc_titles: Sequence[str]) -> str:
    if not doc_titles:
        return "No docs filed here yet."
    return "Already holds: " + "; ".join(doc_titles[:20])


_HEADING = re.compile(r"^\s{0,3}#{1,6}\s")
_LINE_MARKER = re.compile(r"^\s*(?:>\s*|[-*+]\s+|\d+[.)]\s+)")
"""A quote, bullet or number opening a line."""
_MARKUP = re.compile(r"[*`]+|(?<!\w)_+|_+(?!\w)")
"""Emphasis and code marks — an underscore only at a word's edge, so
``write_doc`` keeps its own."""


def summary_of(body: str) -> str:
    """The opening paragraph of a doc's markdown, headings skipped, as one line.

    Docs carry no summary field; their first paragraph is what one would say.
    """
    for paragraph in re.split(r"\n\s*\n", body):
        lines = [
            _LINE_MARKER.sub("", line)
            for line in paragraph.splitlines()
            if not _HEADING.match(line)
        ]
        text = " ".join(_MARKUP.sub("", " ".join(lines)).split())
        if text:
            if len(text) <= SUMMARY_MAX_CHARS:
                return text
            return text[: SUMMARY_MAX_CHARS - 1].rstrip() + "…"
    return ""
