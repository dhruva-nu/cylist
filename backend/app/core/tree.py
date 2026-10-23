"""Walking a parent-linked hierarchy held in memory.

Folders, vault nodes and the rest of the app's trees are each a table whose
rows point at their parent, and each used to carry its own few lines to ask the
same three questions: what is above this, is this inside that, and where does
this sit. They are written once here, over anything with an ``id`` and a
``parent_id``. What lives in the database — a subtree, the chain above one row
— is :mod:`app.services.trees`.

Every walk is bounded by the nodes it has been given, which is what makes a
cycle in the data a short answer instead of a hung request: a ring of rows
parented to each other is a bug somebody has to find, and it must not be found
by a request that never returns.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol
from uuid import UUID


class Node(Protocol):
    """What a tree walk needs of a row."""

    @property
    def id(self) -> UUID: ...

    @property
    def parent_id(self) -> UUID | None: ...


def ancestors[N: Node](nodes: Mapping[UUID, N], node: N, *, limit: int | None = None) -> list[N]:
    """Every node above ``node`` that ``nodes`` holds, nearest first.

    Stops at a parent missing from ``nodes`` — a row whose parent is somehow
    gone costs itself and nothing else — at a node already seen, and after
    ``limit`` of them when one is given.
    """
    found: list[N] = []
    seen = {node.id}
    cursor = node.parent_id
    while cursor is not None and cursor not in seen and (limit is None or len(found) < limit):
        parent = nodes.get(cursor)
        if parent is None:
            break
        found.append(parent)
        seen.add(cursor)
        cursor = parent.parent_id
    return found


def is_inside[N: Node](nodes: Mapping[UUID, N], node: N, ancestor_id: UUID) -> bool:
    """Whether ``ancestor_id`` is somewhere above ``node``.

    Answered from the parent links alone, so it is true even where the ancestor
    itself is not in ``nodes``.
    """
    seen = {node.id}
    cursor = node.parent_id
    while cursor is not None and cursor not in seen:
        if cursor == ancestor_id:
            return True
        seen.add(cursor)
        parent = nodes.get(cursor)
        if parent is None:
            return False
        cursor = parent.parent_id
    return False
