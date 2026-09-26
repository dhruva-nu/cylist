"""Typed answers, token usage and the `Result` of one `ask(...)`.

The API keys score levels by index ("0", "1", ...); answers here are relabelled with the
question's own criteria labels, so `result[Urgency].probabilities["critical"]` just works.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Iterator, List, Mapping, Optional, Sequence, Tuple, Type, Union

from ._errors import JevResponseError
from ._questions import JSON, Question


@dataclass(frozen=True)
class ChoiceAnswer:
    choice: str  # the label with the highest probability
    probabilities: Dict[str, float]  # label -> probability, in criteria order
    confidence: float  # 0..1; low values are worth a human look

    @property
    def probability(self) -> float:
        """P(the chosen label)."""
        return self.probabilities[self.choice]

    def ranked(self) -> List[Tuple[str, float]]:
        """(label, probability) pairs, most likely first."""
        return sorted(self.probabilities.items(), key=lambda kv: kv[1], reverse=True)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": "choice",
            "choice": self.choice,
            "confidence": self.confidence,
            "probabilities": dict(self.probabilities),
        }


@dataclass(frozen=True)
class ScoreAnswer:
    score: float  # expected level index: 1.4 means "between level 1 and level 2"
    probabilities: Dict[str, float]  # level label -> probability, in level order
    legend: Dict[str, JSON]  # level label -> description
    confidence: float

    @property
    def level(self) -> int:
        """The nearest level index to the expected score."""
        return min(max(int(self.score + 0.5), 0), len(self.probabilities) - 1)

    @property
    def label(self) -> str:
        """The label of the nearest level."""
        return list(self.probabilities)[self.level]

    def to_dict(self) -> Dict[str, Any]:
        # The wire format keys levels by index, whatever they are called in Python.
        by_index = [str(i) for i in range(len(self.probabilities))]
        return {
            "type": "score",
            "score": self.score,
            "confidence": self.confidence,
            "legend": dict(zip(by_index, self.legend.values())),
            "probabilities": dict(zip(by_index, self.probabilities.values())),
        }


@dataclass(frozen=True)
class NoulAnswer:
    probability: float  # P(yes / true)

    def is_true(self, threshold: float = 0.5) -> bool:
        return self.probability >= threshold

    def to_dict(self) -> Dict[str, Any]:
        return {"type": "noul", "noul": self.probability}


Answer = Union[ChoiceAnswer, ScoreAnswer, NoulAnswer]


@dataclass(frozen=True)
class Usage:
    input_tokens: int
    output_tokens: int = 0  # currently free of charge


@dataclass(frozen=True)
class Model:
    name: str  # pass as `model=` to the client
    description: str
    release_date: str


class Result:
    """Answers to one `ask(...)`, addressable by question class or by name."""

    def __init__(
        self,
        answers: Dict[str, Answer],
        usage: Usage,
        model: str,
        request_id: Optional[str] = None,
        raw: Optional[Dict[str, Any]] = None,
    ):
        self.answers = answers
        self.usage = usage
        self.model = model  # the model that answered, e.g. "jev-1.13.0" for alias "jev-latest"
        self.request_id = request_id  # quote this when reporting a problem to TypeSafe
        self.raw = raw  # the response body as received
        self.elapsed: Optional[float] = None  # seconds for the whole call, retries included (set by the client)
        self.attempts = 1  # 1 + retries

    def __getitem__(self, key: Union[str, Type[Question]]) -> Answer:
        return self.answers[key if isinstance(key, str) else key.name]

    def __contains__(self, key: object) -> bool:
        name = key if isinstance(key, str) else getattr(key, "name", None)
        return name in self.answers

    def __iter__(self) -> Iterator[str]:
        return iter(self.answers)

    def __len__(self) -> int:
        return len(self.answers)

    def items(self):
        return self.answers.items()

    def to_dict(self) -> Dict[str, Any]:
        """The Jev response shape."""
        return {
            "model": self.model,
            "answers": {name: a.to_dict() for name, a in self.answers.items()},
            "usage": {"input_tokens": self.usage.input_tokens, "output_tokens": self.usage.output_tokens},
        }

    def __repr__(self):
        return "Result(model=%r, %s)" % (self.model, ", ".join("%s=%r" % kv for kv in self.answers.items()))


# ----------------------------------------------------------------------------- parsing
def parse_result(
    questions: Sequence[Type[Question]], payload: Any, request_id: Optional[str] = None
) -> Result:
    """A typed Result from a /v1/systemone response body, relabelled with each question's criteria."""
    try:
        wire_answers = payload["answers"]
        answers = {q.name: parse_answer(q, wire_answers[q.name]) for q in questions}
        usage = payload.get("usage") or {}
        return Result(
            answers,
            Usage(int(usage.get("input_tokens", 0)), int(usage.get("output_tokens", 0))),
            payload.get("model", ""),
            request_id,
            payload,
        )
    except JevResponseError:
        raise
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise JevResponseError("unexpected response shape (%s: %s)" % (type(exc).__name__, exc), payload) from exc


def parse_answer(question: Type[Question], wire: Mapping[str, Any]) -> Answer:
    if wire.get("type") != question.type:
        raise JevResponseError(
            "question %r is a %s but was answered as %r" % (question.name, question.type, wire.get("type")), wire
        )
    labels = question.labels()
    if question.type == "choice":
        return ChoiceAnswer(
            choice=wire["choice"],
            probabilities={label: float(wire["probabilities"][label]) for label in labels},
            confidence=float(wire["confidence"]),
        )
    if question.type == "score":
        by_index = [str(i) for i in range(len(labels))]
        return ScoreAnswer(
            score=float(wire["score"]),
            probabilities={label: float(wire["probabilities"][i]) for label, i in zip(labels, by_index)},
            legend={label: wire["legend"][i] for label, i in zip(labels, by_index)},
            confidence=float(wire["confidence"]),
        )
    return NoulAnswer(probability=float(wire["noul"]))


def parse_models(payload: Any) -> List[Model]:
    try:
        return [Model(m["name"], m.get("description", ""), m.get("release_date", "")) for m in payload["models"]]
    except (KeyError, TypeError) as exc:
        raise JevResponseError("unexpected /v1/models response (%s)" % exc, payload) from exc
