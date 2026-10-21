"""Reading the human references people type: ``ATL-41``, ``ATL-41-2``, ``ATL-G1``.

One parser for all three, so that what counts as a number is decided once.
``str.isdigit`` is the wrong question: it says yes to ``²`` and to ``٣``,
which ``int`` then refuses or reads as a digit nobody typed, and it has no
upper bound, so a 4,300-digit "number" reaches ``int`` and one past int32
reaches Postgres. Either way the answer was a 500 rather than "no such card".

A number here is ASCII digits, at most nine of them — every value fits in the
``integer`` column it is compared against, and no project will ever hand out a
billionth card.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

_KEY: Final = r"([A-Za-z0-9]+)"
_NUMBER: Final = r"(\d{1,9})"

_TASK: Final = re.compile(rf"{_KEY}-{_NUMBER}(?:-{_NUMBER})?", re.ASCII)
_TOO_DEEP: Final = re.compile(rf"{_KEY}(?:-{_NUMBER}){{3,}}", re.ASCII)
_GOAL: Final = re.compile(rf"{_KEY}-G{_NUMBER}", re.ASCII | re.IGNORECASE)


@dataclass(frozen=True)
class TaskReference:
    """``ATL-41`` (``sub_number`` is ``None``) or ``ATL-41-2``."""

    key: str
    """Upper-cased, as project keys are stored."""
    number: int
    sub_number: int | None


@dataclass(frozen=True)
class GoalReference:
    """``ATL-G1``."""

    key: str
    number: int


def task_reference(text: str) -> TaskReference | None:
    """The card or sub-task ``text`` names, or ``None`` if it is not shaped like one."""
    match = _TASK.fullmatch(text)
    if match is None:
        return None
    key, number, sub_number = match.groups()
    return TaskReference(
        key=key.upper(),
        number=int(number),
        sub_number=None if sub_number is None else int(sub_number),
    )


def is_too_deep(text: str) -> bool:
    """Whether ``text`` is a task reference with more numbers than sub-tasks go deep.

    So the refusal can say why: ``ATL-41-2-1`` is a reasonable guess at a
    sub-task of a sub-task, which is a rule to explain rather than a typo.
    """
    return _TOO_DEEP.fullmatch(text) is not None


def goal_reference(text: str) -> GoalReference | None:
    """The goal ``text`` names, or ``None`` if it is not shaped like one.

    Case-insensitive on the key and on the ``G``: a reference read out loud
    arrives in whatever case the reader typed it.
    """
    match = _GOAL.fullmatch(text)
    if match is None:
        return None
    key, number = match.groups()
    return GoalReference(key=key.upper(), number=int(number))
