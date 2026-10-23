"""Cold storage: where a board's finished cards go once nobody needs to see them.

The board's last column is where work ends, and it only ever fills up. Cold
storage empties it: every card in it comes off the board in one go, and a CSV
of what they were is filed in the project's ``Cold storage`` folder, so the
record of what got done is a file anybody can open in a spreadsheet.

Three choices here are worth stating:

* **The rows stay.** A stored card leaves its column, not the database: its
  reference still opens it, its history and comments are still on it, a goal
  still counts it as done, and it can be put back. The CSV is the readable
  record, not the only one — a file can be deleted by anybody with ``files``,
  and the work it describes should not go with it.
* **The whole column, or nothing.** Storing is a tidy-up of the end of the
  board, not a judgement about any one card; the CSV is one run's worth, named
  for when it was made, which is what makes a folder of them read as a log.
* **Restoring is a return, not a move.** The card goes back to the board's
  last column — wherever that is now — still finished, still on the outcome it
  ended on when there still is one, so nothing about how the work ended is
  forgotten on the way out and back.
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.core.errors import UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.file import FileItem, Folder
from app.models.project import Project
from app.models.task import Task, card_loads
from app.services import columns, files, templates
from app.services.paging import PageRequest, Window, keyset
from app.services.tasks.history import FieldChange, field_change
from app.services.tasks.lookup import LISTED_WITHOUT
from app.services.tasks.moves import refuse_open_work
from app.services.tasks.stacking import cards_in_column, restack
from app.storage import BlobStore

FOLDER_NAME = "Cold storage"
"""Where the CSVs are filed: a folder at the top of the project's files."""

CSV_MIME = "text/csv"

CSV_HEADER = (
    "reference",
    "title",
    "type",
    "priority",
    "status",
    "column",
    "outcome",
    "assignee",
    "goal",
    "template",
    "due_date",
    "created_at",
    "finished_at",
    "stored_at",
    "jira",
    "pull_requests",
    "subtasks",
    "description",
)
"""One row per card, in this order. Names rather than ids throughout: the file
is for a person with a spreadsheet, who has no way to look an id up."""

_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r")
"""What a spreadsheet takes a cell starting with to be a formula.

A card's title is anybody's free text, and ``=HYPERLINK(...)`` in one would be
run by whoever opens the export. Such a cell is written with a leading ``'``,
which spreadsheets read as "this is text" and do not show.
"""


@dataclass(frozen=True)
class Stored:
    """What one trip to cold storage did: the cards it took, and the file it wrote."""

    cards: list[Task]
    file: FileItem
    column: BoardColumn


async def store_finished(
    session: AsyncSession,
    store: BlobStore,
    project: Project,
    *,
    added_by: UUID | None,
    max_bytes: int,
    now: datetime | None = None,
) -> Stored:
    """Take every card in the board's last column off the board, into a CSV.

    The cards go top to bottom, which is the order the file lists them in. Each
    keeps ``finished_at`` and ``outcome_index``; what it loses is its column and
    its place in it.

    Raises:
        UnprocessableRequestError: if the last column is empty — a CSV of no
            cards is a file somebody has to open to find out it is empty.
    """
    finished = await columns.last(session, project.id)
    cards = await cards_in_column(session, finished.id)
    if not cards:
        raise UnprocessableRequestError(
            f"{finished.name} is empty, so there is nothing to put in cold storage.",
            details={"column": finished.name},
        )

    stored_at = now or clock.now()
    data = await _csv_of(session, cards, finished, stored_at)
    folder = await files.folder_in_root(session, project, FOLDER_NAME)
    name = await _free_name(session, folder, project, stored_at)
    item = await files.store_generated(
        session,
        store,
        folder,
        name=name,
        data=data,
        mime=CSV_MIME,
        max_bytes=max_bytes,
        added_by=added_by,
    )

    for card in cards:
        card.archived_at = stored_at
        # A card in the last column is finished, but one that was there before
        # Cylist kept the moment may not say when; cold storage is read by when
        # its cards finished, so a stored card has to answer.
        if card.finished_at is None:
            card.finished_at = stored_at
        # Through the relationship rather than the id, so the card in hand
        # stops pointing at the column it left without a reload per card.
        card.column = None
        card.position = None
    await session.flush()
    return Stored(cards=cards, file=item, column=finished)


async def restore(session: AsyncSession, task: Task) -> tuple[Task, list[FieldChange]]:
    """Put a stored card back at the top of the board's last column.

    Still finished, and on the outcome it ended on if the column still has one
    there — a list shortened since the card left lands it on the last section
    still standing, as :func:`app.services.columns.update` does for the cards
    that never left.

    Held to what any card arriving in that column is held to: its template has
    to allow the column, and nothing under it may still be open.

    Raises:
        UnprocessableRequestError: if the card is not in cold storage, if its
            template keeps it out of the last column, or if a sub-task or a
            checklist item under it is open.
    """
    if not task.in_cold_storage:
        raise UnprocessableRequestError(
            f"{task.reference} is not in cold storage, so there is nothing to restore.",
            details={"reference": task.reference},
        )

    finished = await columns.last(session, task.project_id)
    templates.require_permitted(task, finished)
    await refuse_open_work(session, task, finished, act="restoring it")

    await restack(session, task, finished, 0)
    task.archived_at = None
    task.outcome_index = _outcome_back(task.outcome_index, finished)
    if task.finished_at is None:
        task.finished_at = clock.now()
    await session.flush()
    await session.refresh(task, ["column"])
    return task, [field_change("column", "column", None, finished.name)]


async def stored_page(session: AsyncSession, project: Project, page: PageRequest) -> Window[Task]:
    """One page of the project's cards in cold storage, most recently finished first.

    By when the work finished rather than when it was stored: a run stores a
    whole column at one instant, so every card in it shares an ``archived_at``
    and that order says nothing; when each was finished is what a reader of
    a log of finished work is looking for.
    """
    return await keyset(
        session,
        select(Task)
        .options(*card_loads())
        .where(Task.project_id == project.id, Task.archived_at.is_not(None)),
        keys=(Task.finished_at, Task.id),
        page=page,
        descending=True,
        options=LISTED_WITHOUT,
    )


def stored_outcome(task: Task, last_column: BoardColumn) -> str | None:
    """How a stored card ended, as the label it would come back on.

    A card in cold storage is in no column, so :attr:`Task.outcome` has
    nothing to read its index against. The column it left is the board's last,
    and that is the one restoring it lands it in — so its label is read there,
    by the same rule :func:`restore` follows.
    """
    if task.outcome_index is None:
        return None
    index = _outcome_back(task.outcome_index, last_column)
    return None if index is None else last_column.outcomes[index]


def _outcome_back(index: int | None, column: BoardColumn) -> int | None:
    """Which of ``column``'s outcomes a returning card lands on."""
    if not column.outcomes:
        return None
    return min(index or 0, len(column.outcomes) - 1)


async def _free_name(session: AsyncSession, folder: Folder, project: Project, at: datetime) -> str:
    """The file's name: the project and the moment, numbered if that is taken.

    To the second, so two runs a minute apart are two files — and numbered past
    that, because two people pressing the button at once is not a reason for
    the second of them to be refused.
    """
    stem = f"{project.key}-cold-storage-{at.astimezone(UTC):%Y-%m-%d-%H%M%S}"
    name = f"{stem}.csv"
    attempt = 2
    while not await files.name_is_free(session, folder, name):
        name = f"{stem}-{attempt}.csv"
        attempt += 1
    return name


async def _csv_of(
    session: AsyncSession, cards: list[Task], column: BoardColumn, stored_at: datetime
) -> bytes:
    """The cards as a CSV: UTF-8 with a byte-order mark, one row each.

    The mark is for Excel, which otherwise reads UTF-8 as the local code page
    and turns every accented name into mojibake; everything else skips it.
    """
    under = await _subtasks_of(session, [card.id for card in cards])
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\r\n")
    writer.writerow(CSV_HEADER)
    for card in cards:
        writer.writerow(
            [
                _cell(value)
                for value in (
                    card.reference,
                    card.title,
                    card.type.value,
                    card.priority.value.upper(),
                    card.status.label,
                    column.name,
                    card.outcome or "",
                    card.assignee.name,
                    card.goal.name if card.goal else "",
                    card.template.name if card.template else "",
                    card.due_date.isoformat() if card.due_date else "",
                    _when(card.created_at),
                    _when(card.finished_at),
                    _when(stored_at),
                    card.jira_ref or "",
                    "; ".join(f"{ref['url']} ({ref['state']})" for ref in card.pr_refs),
                    "; ".join(
                        f"{sub.reference} {sub.title} ({_settled_as(sub)})"
                        for sub in under.get(card.id, [])
                    ),
                    card.description,
                )
            ]
        )
    return ("﻿" + buffer.getvalue()).encode("utf-8")


async def _subtasks_of(session: AsyncSession, card_ids: list[UUID]) -> dict[UUID, list[Task]]:
    """Every card's sub-tasks, in one query rather than one per card."""
    rows = await session.scalars(
        select(Task)
        .options(*card_loads())
        .where(Task.parent_id.in_(card_ids))
        .order_by(Task.sub_number)
    )
    by_card: dict[UUID, list[Task]] = defaultdict(list)
    for sub in rows:
        if sub.parent_id is not None:
            by_card[sub.parent_id].append(sub)
    return by_card


def _settled_as(sub: Task) -> str:
    """How a sub-task stands, in a word."""
    if sub.finished_at is not None:
        return "finished"
    return "cancelled" if sub.is_settled else "open"


def _when(moment: datetime | None) -> str:
    """A timestamp as a spreadsheet will parse it: UTC, to the second."""
    return moment.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S") if moment else ""


def _cell(value: str) -> str:
    """A value made safe to open: see :data:`_FORMULA_LEADS`."""
    return f"'{value}" if value.startswith(_FORMULA_LEADS) else value
