"""The order cards are stacked in within a column.

Positions are contiguous from zero in every column, always: anything that takes
a card out of a stack or puts one in renumbers the stack before it is done.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.board import BoardColumn
from app.models.task import Task


async def cards_in_column(session: AsyncSession, column_id: UUID) -> list[Task]:
    """The cards in one column, top to bottom."""
    return list(
        await session.scalars(
            select(Task).where(Task.column_id == column_id).order_by(Task.position)
        )
    )


async def count_cards_in_column(session: AsyncSession, column_id: UUID) -> int:
    """How tall a column's stack is — and so the position at its bottom."""
    total = await session.scalar(
        select(func.count()).select_from(Task).where(Task.column_id == column_id)
    )
    return total or 0


async def restack(session: AsyncSession, task: Task, column: BoardColumn, position: int) -> None:
    """Put a card into a column's stack at ``position`` and renumber the stack.

    ``position`` is clamped to the bottom of the stack, and the card is taken
    out of the list first, so a move within one column does not count it twice.
    """
    stack = [card for card in await cards_in_column(session, column.id) if card.id != task.id]
    stack.insert(min(position, len(stack)), task)
    task.column_id = column.id
    for index, card in enumerate(stack):
        card.position = index


async def renumber(session: AsyncSession, column_id: UUID) -> None:
    """Close the gap a departed task left, keeping positions contiguous."""
    for position, task in enumerate(await cards_in_column(session, column_id)):
        task.position = position
    await session.flush()
