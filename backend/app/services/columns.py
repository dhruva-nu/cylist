"""Board columns, and the rules that keep a board a board.

A board holds between two and eight columns. Both ends are enforced here
rather than in the database, because "how many columns are there" is a count
across rows that no ``CHECK`` constraint can see.

The board's **last** column is the same kind of fact, and it carries two
meanings the others do not: a card in it is finished — see
:func:`app.services.tasks.move` — and it alone may be divided into *outcomes*,
the up-to-three sections that say how the work ended. Both follow a position
rather than a column, so both are checked here and in ``tasks`` rather than
written into either row.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.models.board import MAX_COLUMNS, MIN_COLUMNS, BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.schemas.columns import ColumnCreate, ColumnUpdate

STARTER_COLUMNS: tuple[ColumnCreate, ...] = (
    ColumnCreate(name="To do", description="Agreed and waiting for someone to pick it up."),
    ColumnCreate(name="Done", description="Finished, reviewed and needing nothing further."),
)
"""What a brand new board starts with.

Two, because that is the minimum and anything more is a guess about how this
particular project works. They are named and described so the board is usable
the moment it exists rather than after a setup step.
"""


async def seed(session: AsyncSession, project: Project) -> list[BoardColumn]:
    """Give a new project its starter columns."""
    created = [
        BoardColumn(
            project_id=project.id,
            name=starter.name,
            description=starter.description,
            position=position,
        )
        for position, starter in enumerate(STARTER_COLUMNS)
    ]
    session.add_all(created)
    await session.flush()
    return created


async def list_for_project(session: AsyncSession, project: Project) -> list[BoardColumn]:
    """Return the board's columns, left to right."""
    return list(
        await session.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project.id)
            .order_by(BoardColumn.position)
        )
    )


async def get(session: AsyncSession, column_id: UUID) -> BoardColumn:
    column = await session.get(BoardColumn, column_id)
    if column is None:
        raise NotFoundError("No column with that id.")
    return column


async def first(session: AsyncSession, project: Project) -> BoardColumn:
    """The leftmost column — where every new task lands.

    Raises:
        UnprocessableRequestError: if the board has no columns at all, which
            only a hand-edited database can produce.
    """
    column = await session.scalar(
        select(BoardColumn)
        .where(BoardColumn.project_id == project.id)
        .order_by(BoardColumn.position)
        .limit(1)
    )
    if column is None:
        raise UnprocessableRequestError("This project has no board columns to put a task in.")
    return column


async def last(session: AsyncSession, project_id: UUID) -> BoardColumn:
    """The rightmost column — where finished work ends up.

    Named rather than configured: the board's own order says which column is
    the end of the line, and a second "which one means done?" setting would be
    a thing to keep in step with it.

    Raises:
        UnprocessableRequestError: if the board has no columns at all.
    """
    column = await session.scalar(
        select(BoardColumn)
        .where(BoardColumn.project_id == project_id)
        .order_by(BoardColumn.position.desc())
        .limit(1)
    )
    if column is None:
        raise UnprocessableRequestError("This project has no board columns.")
    return column


async def count(session: AsyncSession, project_id: UUID) -> int:
    total = await session.scalar(
        select(func.count()).select_from(BoardColumn).where(BoardColumn.project_id == project_id)
    )
    return total or 0


async def task_counts(session: AsyncSession, project_id: UUID) -> dict[UUID, int]:
    """How many cards sit in each of a project's columns, in one query.

    Sub-tasks are in no column, so they are not counted anywhere: the number on
    a column is how many cards are in it, and a card that was split into three
    is one card in one column.
    """
    rows = await session.execute(
        select(Task.column_id, func.count())
        .where(Task.project_id == project_id, Task.column_id.is_not(None))
        .group_by(Task.column_id)
    )
    # The `is_not(None)` above is what makes every key a column; the type of the
    # mapped attribute cannot say so, so the narrowing is spelled out here.
    return {column_id: total for column_id, total in rows.tuples() if column_id is not None}


async def create(session: AsyncSession, project: Project, data: ColumnCreate) -> BoardColumn:
    """Add a column to the right of the existing ones.

    Raises:
        ConflictError: if the board is already at its eight-column limit.
    """
    existing = await count(session, project.id)
    if existing >= MAX_COLUMNS:
        raise ConflictError(
            f"A board keeps at most {MAX_COLUMNS} columns.",
            details={"column_count": existing, "max_columns": MAX_COLUMNS},
        )

    column = BoardColumn(
        project_id=project.id,
        name=data.name,
        description=data.description,
        position=existing,
        outcomes=data.outcomes,
    )
    session.add(column)
    await session.flush()
    await _strip_stale_outcomes(session, project.id)
    return column


async def update(session: AsyncSession, column: BoardColumn, data: ColumnUpdate) -> BoardColumn:
    """Apply a partial update. Position moves through :func:`reorder`.

    Rewriting the outcomes settles the cards standing in them in the same
    breath, because a card pointing past the end of a shorter list is in a
    section that is not there — see :func:`_resettle`.

    Raises:
        UnprocessableRequestError: if outcomes are given for a column that is
            not the board's last.
    """
    fields = data.model_dump(exclude_unset=True)
    outcomes = fields.get("outcomes")
    if outcomes is not None:
        await _require_last(session, column, outcomes)

    for field, value in fields.items():
        setattr(column, field, value)
    await session.flush()

    if outcomes is not None:
        await _resettle(session, column)
    return column


async def _require_last(session: AsyncSession, column: BoardColumn, outcomes: list[str]) -> None:
    """Refuse outcomes anywhere but at the end of the board.

    An outcome is how a piece of work *ended*, and nothing ends in the middle
    of a board: sections on a column cards still move out of would be a second
    kind of column, drawn like an ending and meaning nothing of the sort.
    Clearing them is allowed anywhere, so a column that loses the last place
    to a new one on its right can be tidied up.
    """
    if not outcomes:
        return
    finished = await last(session, column.project_id)
    if finished.id == column.id:
        return
    raise UnprocessableRequestError(
        f"Only the board's last column can be divided into outcomes, and that is "
        f"{finished.name}. {column.name} is one cards still move out of.",
        details={"last_column": finished.name, "last_column_id": str(finished.id)},
    )


async def _resettle(session: AsyncSession, column: BoardColumn) -> None:
    """Put the cards in this column back into a section that exists.

    A shortened list leaves cards pointing past its end; they land in the last
    section still standing, which is the nearest thing to where they were. An
    emptied list takes every card out of a section, because there are none.
    """
    cards = list(
        await session.scalars(
            select(Task).where(Task.column_id == column.id, Task.outcome_index.is_not(None))
        )
    )
    ceiling = len(column.outcomes) - 1
    for card in cards:
        card.outcome_index = None if ceiling < 0 else min(card.outcome_index or 0, ceiling)
    if cards:
        await session.flush()


async def delete(session: AsyncSession, column: BoardColumn) -> None:
    """Remove an empty column.

    Raises:
        ConflictError: if the column still holds tasks, or if losing it would
            take the board below two columns.
    """
    project_id = column.project_id

    held = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project_id, Task.column_id == column.id)
    )
    if held:
        raise ConflictError(
            "Move this column's tasks somewhere else before deleting it.",
            details={"task_count": held},
        )

    existing = await count(session, project_id)
    if existing - 1 < MIN_COLUMNS:
        raise ConflictError(
            f"A board keeps at least {MIN_COLUMNS} columns.",
            details={"column_count": existing, "min_columns": MIN_COLUMNS},
        )

    await session.delete(column)
    await session.flush()
    await _renumber(session, project_id)
    await _strip_stale_outcomes(session, project_id)


async def reorder(
    session: AsyncSession, project: Project, column_ids: list[UUID]
) -> list[BoardColumn]:
    """Set the left-to-right order of the whole board.

    Raises:
        UnprocessableRequestError: unless the ids are exactly this project's
            columns. A partial list has no unambiguous meaning, and silently
            guessing where the omitted columns go is worse than refusing.
    """
    columns = await list_for_project(session, project)
    by_id = {column.id: column for column in columns}

    if len(column_ids) != len(set(column_ids)) or set(column_ids) != set(by_id):
        raise UnprocessableRequestError(
            "Name every column on this board exactly once.",
            details={"expected_column_ids": [str(column.id) for column in columns]},
        )

    for position, column_id in enumerate(column_ids):
        by_id[column_id].position = position
    await session.flush()
    await _strip_stale_outcomes(session, project.id)
    return [by_id[column_id] for column_id in column_ids]


async def _strip_stale_outcomes(session: AsyncSession, project_id: UUID) -> None:
    """Take the outcomes off any column that is no longer the board's last.

    Last is a position, so a column loses the title without being touched: a
    new column is added to its right, the board is reordered, the column that
    was last is deleted. Whichever it was, sections left standing in the middle
    of a board would go on saying work ended there, and cards would go on
    sitting in them.
    """
    board = list(
        await session.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project_id)
            .order_by(BoardColumn.position)
        )
    )
    for column in board[:-1]:
        if column.outcomes:
            column.outcomes = []
            await session.flush()
            await _resettle(session, column)


async def _renumber(session: AsyncSession, project_id: UUID) -> None:
    """Close the gap a deleted column left, keeping positions contiguous."""
    columns = await session.scalars(
        select(BoardColumn)
        .where(BoardColumn.project_id == project_id)
        .order_by(BoardColumn.position)
    )
    for position, column in enumerate(columns):
        column.position = position
    await session.flush()
