"""Declarative questions: subclass Choice / Score / Noul and declare Criteria.

    class Department(Choice):
        instructions = "Which department should handle this request?"
        billing   = Criteria("invoices, payments, refunds")
        technical = Criteria("bugs, outages, system errors")
        other     = Criteria()                       # label only

    class Urgency(Score):
        instructions = "How urgent is this request?"
        low      = Criteria("can wait")              # level 0
        soon     = Criteria("this week")             # level 1
        critical = Criteria("today")                 # level 2

    class ChurnRisk(Noul):
        instructions = "Does the user threaten to cancel or leave?"
        true_means  = "customer says they will cancel or switch"   # optional
        false_means = "no such threat"                             # optional

A question's name (the key it is sent and answered under) is its snake_cased class name
(ChurnRisk -> "churn_risk") unless it sets `name`. Declarations are validated when the class
is defined, so mistakes surface at import time rather than as a 422 from the API.

Questions only known at runtime are built with `choice(...)`, `score(...)` and `noul(...)`.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Mapping, Optional, Sequence, Type, Union

# Instructions and criteria descriptions may be any JSON value; strings are the common case.
JSON = Any


# ----------------------------------------------------------------------------- declaration
class Criteria:
    """One option of a Choice or one level of a Score.

    The attribute it is assigned to becomes its label; `description` is what the model reads.
    """

    def __init__(self, description: JSON = None, *, label: str = ""):
        self.description = description
        self.label = label

    def __set_name__(self, owner: type, name: str):
        if not self.label:
            self.label = name

    def __repr__(self):
        return "Criteria(%r)" % (self.description,)


class Question:
    """Base of Choice / Score / Noul. Their subclasses are validated by `__init_subclass__`."""

    type: str = ""  # set by Choice / Score / Noul
    name: str = ""  # defaults to snake_case(class name)
    instructions: JSON = None
    criteria: List[Criteria] = []  # collected in declaration order

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(**kwargs)
        if abstract:
            return
        if "name" not in cls.__dict__:
            cls.name = _snake_case(cls.__name__)
        if not isinstance(cls.name, str) or not cls.name:
            raise TypeError("%s: invalid question name %r" % (cls.__name__, cls.name))
        if cls.instructions is None or (isinstance(cls.instructions, str) and not cls.instructions.strip()):
            raise TypeError("%s: `instructions` must be set" % cls.__name__)
        cls.criteria = _collect_criteria(cls)
        labels = [c.label for c in cls.criteria]
        if len(set(labels)) != len(labels):
            raise TypeError("%s: duplicate criteria labels %r" % (cls.__name__, labels))
        cls._validate()

    def __new__(cls, *args, **kwargs):
        raise TypeError("%s is a question declaration; pass the class itself, not an instance" % cls.__name__)

    @classmethod
    def _validate(cls):
        """Type-specific checks; raise TypeError on a bad declaration."""

    @classmethod
    def to_wire(cls) -> Dict[str, Any]:
        """This question as a Jev question definition."""
        wire: Dict[str, Any] = {"type": cls.type, "instructions": cls.instructions}
        criteria = cls._wire_criteria()
        if criteria is not None:
            wire["criteria"] = criteria
        return wire

    @classmethod
    def _wire_criteria(cls) -> Any:
        raise NotImplementedError

    @classmethod
    def labels(cls) -> List[str]:
        return [c.label for c in cls.criteria]


class Choice(Question, abstract=True):
    """Pick one label out of several. Answered with a `ChoiceAnswer`."""

    type = "choice"

    @classmethod
    def _validate(cls):
        if len(cls.criteria) < 2:
            raise TypeError("%s: a Choice needs at least two Criteria" % cls.__name__)

    @classmethod
    def _wire_criteria(cls) -> Dict[str, JSON]:
        # A label-only option goes out as {label: null}; the API rejects a bare list of labels.
        return {c.label: c.description for c in cls.criteria}


class Score(Question, abstract=True):
    """Place the content on an ordered scale; criteria order is level order. Answered with a `ScoreAnswer`."""

    type = "score"

    @classmethod
    def _validate(cls):
        if len(cls.criteria) < 2:
            raise TypeError("%s: a Score needs at least two Criteria (levels)" % cls.__name__)

    @classmethod
    def _wire_criteria(cls) -> List[JSON]:
        # The API only sees level descriptions, by position; a level with none is described by its label.
        return [c.label if c.description is None else c.description for c in cls.criteria]


class Noul(Question, abstract=True):
    """A yes/no question or statement, answered with P(yes / true) in a `NoulAnswer`."""

    type = "noul"
    true_means: JSON = None
    false_means: JSON = None

    @classmethod
    def _validate(cls):
        if cls.criteria:
            raise TypeError("%s: a Noul takes no Criteria; use `true_means` / `false_means`" % cls.__name__)

    @classmethod
    def _wire_criteria(cls) -> Optional[Dict[str, JSON]]:
        crit = {"true": cls.true_means, "false": cls.false_means}
        return {k: v for k, v in crit.items() if v is not None} or None


QuestionType = Type[Question]


# ----------------------------------------------------------------------------- runtime factories
CriteriaSpec = Union[Mapping[str, JSON], Sequence[str]]


def choice(name: str, instructions: JSON, criteria: CriteriaSpec) -> Type[Choice]:
    """A Choice built at runtime. `criteria` is {label: description-or-None} or a list of labels.

        Tone = choice("tone", "What is the tone?", {"angry": "hostile", "calm": None})
    """
    return _define(Choice, name, instructions, _criteria_list(criteria))


def score(name: str, instructions: JSON, levels: CriteriaSpec) -> Type[Score]:
    """A Score built at runtime. `levels` is a list of descriptions (labelled "0", "1", ...)
    or an ordered {label: description} mapping.

        Urgency = score("urgency", "How urgent?", ["can wait", "this week", "today"])
    """
    if isinstance(levels, Mapping):
        crit = _criteria_list(levels)
    else:
        crit = [Criteria(description, label=str(i)) for i, description in enumerate(levels)]
    return _define(Score, name, instructions, crit)


def noul(name: str, instructions: JSON, true_means: JSON = None, false_means: JSON = None) -> Type[Noul]:
    """A Noul built at runtime.

        Spam = noul("spam", "Is this message spam?", true_means="unsolicited advertising")
    """
    return _define(Noul, name, instructions, [], true_means=true_means, false_means=false_means)


def _define(base: type, name: str, instructions: JSON, criteria: List[Criteria], **attrs) -> Any:
    namespace = {"name": name, "instructions": instructions, "__criteria__": criteria, **attrs}
    return type(_class_name(name), (base,), namespace)


def _criteria_list(criteria: CriteriaSpec) -> List[Criteria]:
    if isinstance(criteria, str):
        raise TypeError("criteria must be a mapping or a list of labels, not a string")
    if isinstance(criteria, Mapping):
        return [Criteria(description, label=str(label)) for label, description in criteria.items()]
    return [Criteria(label=str(label)) for label in criteria]


# ----------------------------------------------------------------------------- helpers
def _collect_criteria(cls: type) -> List[Criteria]:
    """All Criteria on `cls` and its bases, in declaration order (a subclass may add to a base's).

    Factory-built questions carry theirs in `__criteria__`, so labels like "type" or "name"
    cannot collide with class attributes.
    """
    seen: Dict[str, Criteria] = {}
    for klass in reversed(cls.__mro__):
        for value in vars(klass).get("__criteria__", ()):
            seen[value.label] = value
        for name, value in vars(klass).items():
            if isinstance(value, Criteria):
                seen[value.label] = value
    return list(seen.values())


def _snake_case(name: str) -> str:
    s = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s).lower()


def _class_name(name: str) -> str:
    return "".join(part.capitalize() for part in re.split(r"[^A-Za-z0-9]+", name) if part) or "Question"
