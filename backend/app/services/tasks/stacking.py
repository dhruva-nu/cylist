"""The order cards are stacked in within a column.

A card's ``position`` is a sort key, not an index: positions are sparse, with
room left between neighbours, so putting a card somewhere writes that one card
and nothing else. A card dropped between two others takes the key halfway
between theirs; one dropped at the top or the bottom takes a key a gap beyond
the end. Leaving a column leaves a hole, and a hole is harmless — nothing reads
a position as a count.

Only when two neighbours have no integer left between them (a card dropped
into the same slot again and again halves the gap each time) is the column
spread back out, in one statement that touches ids and positions alone.

Every placement holds a lock on the column's own row until the transaction
ends. Two cards dropped into the same slot at once would otherwise both read
the same neighbours and both take the same key; the lock makes the second wait
for the first and read the key it wrote. A placement only ever takes the lock
of the column it is placing into, so two moves in opposite directions cannot
each hold one and wait on the other. ``uq_task_column_id_position`` is the
backstop if anything ever writes a position without asking here first.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Select, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value
from sqlalchemy.orm.util import identity_key

from app.models.board import BoardColumn
from app.models.task import Task

GAP = 1 << 16
"""The room left between neighbours when a key is chosen at either end of a
column, or when a column is spread back out. Sixteen bits buy sixteen drops
into the same slot before it runs out, and still leave room for thirty
thousand cards before a spread-out column would outgrow ``position``'s 32
bits — past that, the spacing shrinks to fit."""

LOWEST = -(2**31)
HIGHEST = 2**31 - 1
"""``position`` is a 32-bit integer, and clients read it as one, so keys are
kept inside that range rather than left to overflow at the database."""


async def cards_in_column(session: AsyncSession, column_id: UUID) -> list[Task]:
    """The cards in one column, top to bottom."""
    return list(
        await session.scalars(
            select(Task).where(Task.column_id == column_id).order_by(Task.position)
        )
    )


async def restack(session: AsyncSession, task: Task, column: BoardColumn, position: int) -> None:
    """Put a card into a column's stack at index ``position``.

    ``position`` counts cards from the top, as a person dragging one does, and
    is clamped to the bottom of the stack. The card is left out of the count,
    so a move within one column does not count it twice. Only the card itself
    is written; its neighbours keep the keys they had.
    """
    task.position = await slot(session, column.id, index=position, moving=task.id)
    task.column_id = column.id


async def slot(
    session: AsyncSession, column_id: UUID, *, index: int | None, moving: UUID | None = None
) -> int:
    """The key a card takes to sit ``index`` cards from the top of a column.

    ``index`` None is the bottom, which is where a new card goes. ``moving`` is
    the card being placed, if it is already in a column, so that it is not
    counted as one of its own neighbours.

    Locks the column for the rest of the transaction before reading anything,
    and spreads the column out first if the slot asked for has no room left.
    """
    await _lock(session, column_id)
    key = await _key_at(session, column_id, index, moving)
    if key is None:
        await respace(session, column_id)
        key = await _key_at(session, column_id, index, moving)
    if key is None:
        # A freshly spread column has room between every pair of keys and at
        # both ends until it holds hundreds of millions of cards.
        raise RuntimeError(f"Column {column_id} has no room for another card.")
    return key


async def respace(session: AsyncSession, column_id: UUID) -> None:
    """Spread a column's keys out evenly, keeping its order.

    One UPDATE: each card's new key is worked out in the database from its
    rank, so no card is loaded to do it. Cards the session already holds are
    told their new keys from what the statement returns.

    The caller holds the column's lock — see :func:`slot`.
    """
    count = await session.scalar(
        select(func.count()).select_from(Task).where(Task.column_id == column_id)
    )
    step = min(GAP, HIGHEST // ((count or 0) + 1))
    ranked = (
        select(
            Task.id.label("id"),
            func.row_number().over(order_by=(Task.position, Task.id)).label("rank"),
        )
        .where(Task.column_id == column_id)
        .subquery()
    )
    spread = await session.execute(
        update(Task)
        .where(Task.id == ranked.c.id)
        # ``updated_at`` kept as it was: nobody touched these cards, and a
        # spread-out column should not read as every card in it just edited.
        .values(position=ranked.c.rank * step, updated_at=Task.updated_at)
        .returning(Task.id, Task.position)
        .execution_options(synchronize_session=False)
    )
    # Told rather than expired: an expired attribute is loaded again on next
    # read, and under asyncio that read is an error, not a query.
    held = session.sync_session.identity_map
    for card_id, position in spread:
        card = held.get(identity_key(Task, card_id))
        if card is not None:
            set_committed_value(card, "position", position)


async def _lock(session: AsyncSession, column_id: UUID) -> None:
    """Hold the column's row until the transaction ends.

    ``FOR NO KEY UPDATE`` rather than ``FOR UPDATE``: it still queues every
    other placement in this column behind this one, but not the key-share lock
    a foreign key check takes, so a card merely pointing at the column is not
    held up being written.
    """
    await session.execute(
        select(BoardColumn.id).where(BoardColumn.id == column_id).with_for_update(key_share=True)
    )


async def _key_at(
    session: AsyncSession, column_id: UUID, index: int | None, moving: UUID | None
) -> int | None:
    """A key between the cards either side of ``index``, or None if there is
    no integer free between them."""
    others = select(Task.position).where(Task.column_id == column_id)
    if moving is not None:
        others = others.where(Task.id != moving)

    if index == 0:
        return _between(None, await _first(session, others.order_by(Task.position)))

    pair: list[int | None] = []
    if index is not None:
        pair = list(
            await session.scalars(others.order_by(Task.position).offset(index - 1).limit(2))
        )
    if not pair:
        # Past the bottom of the stack, which is where an index is clamped to.
        return _between(await _first(session, others.order_by(Task.position.desc())), None)
    return _between(pair[0], pair[1] if len(pair) > 1 else None)


async def _first(session: AsyncSession, query: Select[tuple[int | None]]) -> int | None:
    return await session.scalar(query.limit(1))


def _between(above: int | None, below: int | None) -> int | None:
    """A key strictly between two neighbours, either of which may be the end
    of the column, or None if they leave no room."""
    if below is None:
        if above is None:
            return 0
        return above + GAP if above + GAP <= HIGHEST else None
    if above is None:
        return below - GAP if below - GAP >= LOWEST else None
    if below - above < 2:
        return None
    return above + (below - above) // 2
