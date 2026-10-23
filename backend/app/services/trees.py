"""The parent-linked hierarchies that live in the database.

The same two questions asked of any table whose rows have an ``id`` and a
``parent_id``: which rows are in this one's subtree, and which are above this
one. Each is one recursive query, not a query per level — at the folder depth
limit that is the difference between one round trip and a hundred. The
in-memory walks, for a tree already loaded, are :mod:`app.core.tree`.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import Select, literal, select
from sqlalchemy.ext.asyncio import AsyncSession


def subtree_ids(model: Any, root_id: UUID) -> Select[tuple[UUID]]:
    """Select the ids of a row and every row beneath it.

    A subquery for the caller to ``IN`` against, so deleting or counting a
    branch is one statement however deep it goes.
    """
    subtree = select(model.id).where(model.id == root_id).cte("subtree", recursive=True)
    subtree = subtree.union_all(select(model.id).join(subtree, model.parent_id == subtree.c.id))
    return select(subtree.c.id)


async def ancestors[M](
    session: AsyncSession, model: type[M], parent_id: UUID | None, *, limit: int
) -> list[M]:
    """The rows above a node, nearest first, given the id of its parent.

    At most ``limit + 1`` come back, so a caller holding a depth limit can tell
    "exactly at it" from "past it" without the query following a ring in the
    data forever. A parent that is not there ends the chain.
    """
    if parent_id is None:
        return []
    table: Any = model
    chain = (
        select(table.id, table.parent_id, literal(1).label("level"))
        .where(table.id == parent_id)
        .cte("chain", recursive=True)
    )
    chain = chain.union_all(
        select(table.id, table.parent_id, chain.c.level + 1)
        .join(chain, table.id == chain.c.parent_id)
        .where(chain.c.level <= limit)
    )
    rows = await session.scalars(
        select(table).join(chain, table.id == chain.c.id).order_by(chain.c.level)
    )
    return list(rows)
