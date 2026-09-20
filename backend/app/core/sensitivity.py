"""How sensitive a stored thing is, and how far up a role is cleared to read.

CYLIST-46's first cut fenced *actions* and said outright that reading was not
fenced: a project is a shared workspace, and a board whose cards half the team
cannot see is a different product. That still holds for the board. It does not
hold for what gets *uploaded* to one, which is the thing a client's legal
counsel and a contractor on the same project should not both be looking at.

So uploaded content — a file, a link, a vault entry — carries a **level**, and
each role carries the highest level it may read. Three levels, because two
cannot express "everybody here but not the client" and four is a taxonomy
nobody maintains:

* ``public`` — anybody who can reach the project.
* ``internal`` — the people doing the work.
* ``restricted`` — named roles only.

The names are deliberately the ones already in every company's handbook rather
than Cylist's invention, because the person setting a level is classifying a
document, not learning a tool.

**A level is compared, never matched.** A role cleared to ``internal`` reads
``internal`` and ``public``, which is what makes one field per upload and one
per role enough — see :func:`covers`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Sensitivity(StrEnum):
    """How far something may travel. Ordered by :data:`ORDER`."""

    PUBLIC = "public"
    INTERNAL = "internal"
    RESTRICTED = "restricted"


ORDER: tuple[Sensitivity, ...] = (
    Sensitivity.PUBLIC,
    Sensitivity.INTERNAL,
    Sensitivity.RESTRICTED,
)
"""Least sensitive first. The comparison every read goes through."""

_RANK = {level: index for index, level in enumerate(ORDER)}

DEFAULT_LEVEL = Sensitivity.INTERNAL
"""What an upload is if nobody says.

Not ``public``: a file put on a project is work product, and the level that
errs should be the one that keeps it among the people doing the work. Not
``restricted`` either — that would make every ordinary upload invisible to
most of the board and teach everybody to raise their own clearance.
"""

DEFAULT_CLEARANCE = Sensitivity.RESTRICTED
"""What a role may read if nobody has said.

The whole of the per-object layer works this way: the flat permission is a
grant, so its absence is a refusal, and everything here is a *restriction*, so
its absence is no restriction at all. A project that nobody has classified
behaves exactly as it did before levels existed.
"""


def covers(clearance: Sensitivity, level: Sensitivity) -> bool:
    """Whether a role cleared this far may read something at that level."""
    return _RANK[clearance] >= _RANK[level]


def at_most(clearance: Sensitivity) -> frozenset[Sensitivity]:
    """Every level this clearance can read, for a ``WHERE … IN`` on a list."""
    return frozenset(level for level in ORDER if covers(clearance, level))


@dataclass(frozen=True, slots=True)
class SensitivityInfo:
    """One level, and the words a picker draws it with."""

    key: Sensitivity
    label: str
    summary: str


CATALOGUE: tuple[SensitivityInfo, ...] = (
    SensitivityInfo(
        Sensitivity.PUBLIC,
        "Public",
        "Anybody who can open this project.",
    ),
    SensitivityInfo(
        Sensitivity.INTERNAL,
        "Internal",
        "The people doing the work. What an upload is unless somebody says otherwise.",
    ),
    SensitivityInfo(
        Sensitivity.RESTRICTED,
        "Restricted",
        "Only roles cleared for it. Everybody else is not shown that it exists.",
    ),
)

INFO: dict[Sensitivity, SensitivityInfo] = {entry.key: entry for entry in CATALOGUE}


def parse(value: str | None, fallback: Sensitivity) -> Sensitivity:
    """Read a stored level, falling back rather than raising.

    The reason :func:`~app.auth.permissions.parse_permissions` drops unknown
    values applies here with more force: a level this server does not
    recognise must not make a file unreadable *and* unfixable.
    """
    try:
        return Sensitivity(value) if value is not None else fallback
    except ValueError:
        return fallback
