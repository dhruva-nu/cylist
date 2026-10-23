"""The dates a card is wanted in particular columns by, read against its board.

A card has one ``due_date`` of its own — the day it is wanted at the end of the
board — and may have more for the columns before that. Whether one of those has
been met is never stored: it is where the card is.
"""

from __future__ import annotations

from datetime import date
from typing import NamedTuple
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, Reason, UnprocessableRequestError
from app.db import flush_or_refuse, lock_row
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task, TaskColumnDueDate
from app.schemas.tasks import ColumnDueDateInput
from app.services import columns
from app.services.tasks.history import FieldChange, field_change


class ColumnDue(NamedTuple):
    """A date a card is wanted in one column by, read against the board.

    ``met`` is not stored anywhere: it is where the card is. Once the card has
    reached the column, the day it was wanted there is behind it, and nothing
    further is owed on that date.
    """

    column_id: UUID
    column_name: str
    due_date: date
    met: bool


class BoardDates(NamedTuple):
    """Every date a card owes, and the one it owes next.

    ``next_due`` is the date a card is drawn with: the soonest of the dates it
    has not met yet, the card's own ``due_date`` — the last column's — among
    them. Null once the card is done, which is what takes the overdue mark off
    a finished card and off every column deadline it has already passed.
    """

    per_column: list[ColumnDue]
    next_due: date | None


async def set_column_due_dates(
    session: AsyncSession, task: Task, entries: list[ColumnDueDateInput]
) -> None:
    """Replace the dates a card is wanted in particular columns by.

    The whole set at once, because that is how the dates are read: a schedule
    across the board rather than a field per column. An empty list takes them
    all off.

    The last column is refused rather than accepted and stored twice: a card is
    done when it reaches the end of the board, so the day it is wanted there is
    the card's own ``due_date``, and a second place to write it is a second
    answer that can disagree with the first.

    Raises:
        UnprocessableRequestError: if the task is a sub-task, which is not on
            the board and so passes through no columns; if a column is on
            another project's board; or if one of them is the board's last.
    """
    if not entries and not task.column_due_dates:
        return

    if entries and task.parent_id is not None:
        raise UnprocessableRequestError(
            f"{task.reference} is a sub-task, so it is not on the board and passes through no "
            "columns. Give it a due date of its own instead.",
            details={"reference": task.reference},
        )

    board = await columns.list_for_project(session, task.project)
    _refuse_undatable_columns(entries, board)

    # The card's row is locked first, so two people rescheduling one card at the
    # same moment take turns. Otherwise both delete the rows neither of them
    # can see the other inserting, and the second insert lands on the first's
    # key and fails as a 500.
    await lock_row(session, Project, task.project_id)
    await lock_row(session, Task, task.id)
    # Re-read under the lock: the rows loaded with the card may predate a
    # schedule the other request has since committed, and clearing that stale
    # list would leave the other's rows in place for this insert to collide with.
    await session.refresh(task, ["column_due_dates"])

    # Emptied and flushed before the new rows are added, so replacing a column's
    # date is a delete and an insert rather than an insert onto its own key.
    task.column_due_dates.clear()
    await session.flush()
    task.column_due_dates = [
        TaskColumnDueDate(task_id=task.id, column_id=entry.column_id, due_date=entry.due_date)
        for entry in entries
    ]
    # And a column deleted between the check above and this insert is refused
    # for what it is rather than as the foreign key's 500.
    await flush_or_refuse(
        session,
        ConflictError(
            "The board changed while those dates were being saved. Try again.",
            reason=Reason.CHANGED_CONCURRENTLY,
        ),
    )


def _refuse_undatable_columns(entries: list[ColumnDueDateInput], board: list[BoardColumn]) -> None:
    """Check every dated column is on this card's board and is not its end.

    Raises:
        UnprocessableRequestError: if a column is on another project's board,
            or is the board's last.
    """
    by_id = {column.id: column for column in board}
    for entry in entries:
        column = by_id.get(entry.column_id)
        if column is None:
            raise UnprocessableRequestError(
                "That column is on a different project's board.",
                details={"column_id": str(entry.column_id)},
            )
        if column.id == board[-1].id:
            raise UnprocessableRequestError(
                f"{column.name} is the end of the board, so the day a card is wanted there is "
                "the day the work is wanted done. Send that as `due_date`.",
                details={"column": column.name},
            )


def board_dates(task: Task, board: list[BoardColumn]) -> BoardDates:
    """Read a card's dates against the board it is on.

    Each per-column date is marked met or not by where the card has got to, and
    the soonest of the unmet ones — the card's own ``due_date`` among them — is
    what the card is working towards now. A card in the last column owes
    nothing: it is done, and so is every deadline it passed on the way.

    The soonest rather than the leftmost, because what a date is for is saying
    whether the card is late: the one that will make it late first is the one
    to draw.
    """
    columns_by_id = {column.id: column for column in board}
    current = columns_by_id.get(task.column_id) if task.column_id is not None else None

    entries: list[ColumnDue] = []
    for row in task.column_due_dates:
        column = columns_by_id.get(row.column_id)
        if column is None:
            continue
        entries.append(
            ColumnDue(
                column_id=row.column_id,
                column_name=column.name,
                due_date=row.due_date,
                met=current is not None and current.position >= column.position,
            )
        )
    entries.sort(key=lambda entry: columns_by_id[entry.column_id].position)

    # Off the board — a sub-task, or a card in cold storage — there is no
    # column to read done from, and ``finished_at`` is what says it.
    if task.column_id is None:
        finished = task.finished_at is not None
    else:
        finished = bool(board) and task.column_id == board[-1].id
    pending = [entry.due_date for entry in entries if not entry.met]
    if task.due_date is not None and not finished:
        pending.append(task.due_date)
    return BoardDates(entries, min(pending) if pending else None)


def column_due_dates_of(task: Task) -> dict[UUID, date]:
    """Which columns this card is dated in, and when.

    Copied out of the relationship rather than snapshotted with the rest of the
    fields: the collection is rewritten in place, so a reference to it taken
    beforehand would report the state afterwards.
    """
    return {row.column_id: row.due_date for row in task.column_due_dates}


async def column_dates_change(
    session: AsyncSession, task: Task, before: dict[UUID, date]
) -> FieldChange | None:
    """Describe a change to a card's per-column dates, or nothing if none.

    Read in board order and by column name, the way the dates themselves are
    read: "Review 2026-09-20, QA 2026-09-25" says what changed, where a list of
    column ids says only that something did.
    """
    after = column_due_dates_of(task)
    if after == before:
        return None

    board = await columns.list_for_project(session, task.project)
    return field_change(
        "column_due_dates",
        "column due dates",
        _describe_column_dates(before, board),
        _describe_column_dates(after, board),
    )


def _describe_column_dates(dates: dict[UUID, date], board: list[BoardColumn]) -> str | None:
    """A card's per-column dates as one line, in board order, or None if it has none."""
    said = [
        f"{column.name} {dates[column.id].isoformat()}" for column in board if column.id in dates
    ]
    return ", ".join(said) or None
