"""The audit trail: what people and agents did, and how it reads.

Two halves, kept in modules of their own because they change for different
reasons. :mod:`.trail` writes rows and reads them back — the database side,
including the news a write rings for boards and hooks. :mod:`.wording` turns a
row into a sentence, and is where a new verb is taught to read well.

Callers keep reaching both through ``activity.record``, ``activity.describe``
and the rest: which half a function lives in is this package's business.
"""

from __future__ import annotations

from app.services.activity.trail import (
    between,
    changed_something,
    for_entity,
    recent,
    record,
)
from app.services.activity.wording import describe, entry_of, excerpt, moved

__all__ = [
    "between",
    "changed_something",
    "describe",
    "entry_of",
    "excerpt",
    "for_entity",
    "moved",
    "recent",
    "record",
]
