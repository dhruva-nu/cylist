"""Where a card is on the board, and whether it is done.

A card is finished by arriving in the board's last column and a sub-task by
being ticked off; both write the same ``finished_at``, which is why the two
calls live together here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.task import ChecklistState, Task, TaskChecklistItem, TaskStatus
from app.schemas.tasks import TaskMove
from app.services import columns, goals, templates
from app.services.tasks.history import (
    FieldChange,
    field_change,
    finished_change,
    outcome_change,
    stage_change,
    stage_label,
)
from app.services.tasks.parentage import lock_parent, refuse_open_under_finished
from app.services.tasks.stacking import renumber, restack


async def move(session: AsyncSession, task: Task, data: TaskMove) -> tuple[Task, list[FieldChange]]:
    """Put a task in a column at a position.

    Unlike creation this is unrestricted by direction: once a card is on the
    board it may go anywhere its template allows, including straight back to
    the first column. A card whose template has no stages may go anywhere at
    all.

    A card that changes column starts its stages again. ``sub_statuses`` is
    progress through the column the card is in, not through the board — "drafted,
    reviewed, merged" means one thing in Review and another in Done — so a card
    arriving somewhere new has not begun the stages it keeps there. If the
    card's template names sub-stages for the destination column, those replace
    ``sub_statuses`` outright rather than only resetting the pointer into
    whatever the card already carried. Moving within one column leaves the
    stage alone: nothing has been arrived at.

    Arriving in the board's last column is what finishes a card, and leaving it
    is what reopens one: ``finished_at`` is written on the way in and cleared on
    the way out. A card is not asked to confirm that here — the server records
    what happened, and whoever is dragging the card is the one who decides
    whether it should — but both directions are reported as a change, so a
    reopened card says so in its own history rather than only saying it moved.

    Where that last column is divided into outcomes — "Done", "Cancelled", "In
    prod" — the card lands on the one named, or on the first its template
    allows if the move named none, and a card leaving loses whichever it was
    on. Dragged between two sections of the same column, a card has not moved
    by the board's reckoning but has changed how it ended, and that alone is
    reported.

    Returns the task and what changed about it, in the same shape
    :func:`app.services.tasks.update` reports — the column it left and the one
    it arrived in, the stage it was put back to if that happened, the outcome
    it ended on, and whether it was finished or reopened. Reordering within one
    column changes nothing worth recording: a card's place in a stack is not a
    fact about the work.

    Raises:
        UnprocessableRequestError: if the task is a sub-task, which is not on
            the board and so has nowhere to be moved to; if it is in cold
            storage, which it has to be restored from first; if the column belongs
            to another project, if the card's template is not allowed in it or
            not allowed to end on the outcome named, if the outcome is not one
            of that column's, if the column it is leaving still has the card
            short of the last sub-stage its template set there, if the
            destination is the last column and a sub-task is still open, or if
            the card is leaving the last column while its goal is achieved.
    """
    _refuse_moving_subtask(task)
    _refuse_moving_stored(task)
    destination = await _destination_column(session, task, data.column_id)
    templates.require_permitted(task, destination)
    source_id = _board_column_id(task)
    changes_column = source_id != destination.id
    if changes_column:
        await refuse_stage_incomplete(session, task)
    last_column = await columns.last(session, task.project_id)
    await _refuse_unfinished(session, task, destination, last_column)
    await _refuse_reopening_on_achieved(session, task, destination, last_column)
    landing_outcome = _landing_outcome(task, destination, data.outcome)

    stage_before = stage_label(task.sub_statuses, task.sub_status_index)
    # Read off the source column while the card is still in it. The label the
    # index points at is a fact about a column, and in a moment the card will
    # be in a different one.
    outcome_before = _outcome_label(task.column, task.outcome_index)
    outcome_after = _outcome_label(destination, landing_outcome)

    await restack(session, task, destination, data.position)
    if changes_column:
        _restart_stages(task, destination)
    task.outcome_index = landing_outcome
    await session.flush()
    # The card is in a different column now, and the one still hanging off the
    # relationship is the one it left — which is the column ``Task.outcome``
    # would read its own label out of.
    await session.refresh(task, ["column"])

    changes: list[FieldChange] = []
    if changes_column:
        await renumber(session, source_id)
        source = await columns.get(session, source_id)
        changes.append(field_change("column", "column", source.name, destination.name))
        stage_after = stage_label(task.sub_statuses, task.sub_status_index)
        if stage_after != stage_before:
            changes.append(stage_change(stage_before, stage_after))

    # Dragged between the sections of one column, this is the only change: not
    # a move by the board's reckoning, but a change of how the work ended,
    # which is worth saying.
    if outcome_before != outcome_after:
        changes.append(outcome_change(outcome_before, outcome_after))

    if changes_column:
        finished = _mark_finished_by_column(task, destination, last_column)
        if finished is not None:
            changes.append(finished)
    return task, changes


async def set_finished(
    session: AsyncSession, task: Task, *, finished: bool
) -> tuple[Task, list[FieldChange]]:
    """Finish a sub-task, or reopen a finished one.

    This is what ticking a sub-task off does, and it is the only way a sub-task
    becomes done: there is no last column for it to be dragged into, because it
    is not on the board. A card writes the same ``finished_at`` by being moved
    into that column — see :func:`move` — which is why this refuses one.

    A finished sub-task stops holding its parent back — the same effect
    cancelling it has, said about work that happened rather than work that was
    dropped.

    Finishing something already finished is not an error and does not restate
    the time: the tick box was already ticked, and a second click on it is a
    client catching up rather than a new event to record.

    Returns the task and what changed about it, in the shape
    :func:`app.services.tasks.update` reports, so a history entry reads the
    same however the change was made.

    Reopening one is refused while its parent is finished — see
    :mod:`app.services.tasks.parentage` for why that is a refusal rather than
    the parent quietly reopening too.

    Raises:
        UnprocessableRequestError: if the task is a top-level card, whose done
            is the board's last column and not a field here, or if it is being
            reopened under a parent that is finished.
    """
    if task.parent_id is None:
        raise UnprocessableRequestError(
            f"{task.reference} is a card on the board, so it is finished by being moved to the "
            "board's last column rather than ticked off. Only a sub-task is finished here.",
            details={"reference": task.reference},
        )

    was_finished = task.finished_at is not None
    if was_finished == finished:
        return task, []
    if not finished and task.parent is not None:
        await refuse_open_under_finished(session, task.parent, act=f"reopening {task.reference}")

    task.finished_at = datetime.now(UTC) if finished else None
    await session.flush()
    return task, [finished_change(was_finished=was_finished, now_finished=finished)]


async def refuse_stage_incomplete(session: AsyncSession, task: Task) -> None:
    """Stop a card leaving a column before it is on the last sub-stage its
    template set there.

    Checked on every move out of the column the card sits in, not only into the
    board's last one: a template's stage is a promise about *this* column, and
    the only moment to enforce it is the moment the card tries to leave.

    Raises:
        UnprocessableRequestError: naming the sub-stage still in hand, if the
            current column has sub-stages the card's template set and the card
            has not reached the last one.
    """
    current_column_id = _board_column_id(task)
    stage = templates.stage_for_column(task.template, current_column_id)
    if stage is None or not stage.sub_stage_labels:
        return

    labels = stage.sub_stage_labels
    last = len(labels) - 1
    index = task.sub_status_index if task.sub_status_index is not None else -1
    if index == last:
        return

    here = await columns.get(session, current_column_id)
    current = labels[index] if index >= 0 else "not yet started"
    raise UnprocessableRequestError(
        f'{task.reference} is still on "{current}" in {here.name}. Move it to '
        f'"{labels[last]}" before moving this card out of {here.name}.',
        details={
            "column": here.name,
            "current_sub_stage": None if index < 0 else current,
            "target_sub_stage": labels[last],
        },
    )


def _refuse_moving_subtask(task: Task) -> None:
    """Stop a sub-task being dragged anywhere: it is not on the board.

    Raises:
        UnprocessableRequestError: if the task is a sub-task.
    """
    # `parent` rather than `parent_id`: it is eagerly loaded — a sub-task cannot
    # spell its own reference without it — and naming the card to go and move
    # instead is most of what makes this refusal useful.
    if task.parent is not None:
        raise UnprocessableRequestError(
            f"{task.reference} is a sub-task, so it is not on the board and has no column to "
            f"be moved to. Finish it instead, and move {task.parent.reference} when everything "
            "under it is settled.",
            details={"parent": task.parent.reference},
        )


def _refuse_moving_stored(task: Task) -> None:
    """Stop a card in cold storage being dragged back by a move.

    Restoring is its own act — :func:`app.services.tasks.restore` — because it
    is a return to the board's end, still finished, rather than a card turning
    up wherever it was dropped with no column it is coming from.

    Raises:
        UnprocessableRequestError: if the card is in cold storage.
    """
    if task.in_cold_storage:
        raise UnprocessableRequestError(
            f"{task.reference} is in cold storage, so it is not on the board to be moved. "
            "Restore it first; it goes back to the board's last column.",
            details={"reference": task.reference},
        )


async def _destination_column(session: AsyncSession, task: Task, column_id: UUID) -> BoardColumn:
    """The column a card is being moved into, which must be on its own board.

    Raises:
        UnprocessableRequestError: if the column is on another project's
            board, or on none.
    """
    return await columns.require_column(session, task.project_id, column_id)


async def _refuse_unfinished(
    session: AsyncSession, task: Task, destination: BoardColumn, last_column: BoardColumn
) -> None:
    """Stop a card reaching the last column while a sub-task is still open.

    Enforced on the move rather than on the sub-task, because it is a rule about
    the parent: a sub-task may be left open for as long as anybody likes, and
    the moment that matters is the one where its parent claims to be done.

    ``last_column`` is handed in rather than looked up because :func:`move` has
    already had to find it to know whether the card is arriving at done.

    Raises:
        UnprocessableRequestError: if anything under the task is still open.
    """
    arriving_at_end = destination.id == last_column.id and task.column_id != last_column.id
    if not arriving_at_end:
        return

    await refuse_open_work(session, task, last_column, act="moving this card")


async def _refuse_reopening_on_achieved(
    session: AsyncSession, task: Task, destination: BoardColumn, last_column: BoardColumn
) -> None:
    """Stop a card leaving the last column while the goal it is on is achieved.

    Leaving that column reopens the card, and an achieved goal has no open card
    on it. The goal is locked for the rest of the move, so a request achieving
    it at the same moment waits and then finds this card open. A cancelled card
    is not open wherever it is, so it may go where it likes.

    Raises:
        UnprocessableRequestError: naming the goal, if it is achieved.
    """
    leaving_end = task.column_id == last_column.id and destination.id != last_column.id
    if not leaving_end or task.status is TaskStatus.CANCELLED:
        return
    await goals.refuse_open_card_on_achieved(
        session, task.goal_id, card=task.reference, act=f"moving it out of {last_column.name}"
    )


async def refuse_open_work(
    session: AsyncSession, task: Task, last_column: BoardColumn, *, act: str
) -> None:
    """Refuse to land a card in the last column over work still open under it.

    Shared by a move into that column and a return from cold storage, which
    keep the same rule for the same reason: a card whose parts are open is not
    done, and the last column says it is. ``act`` names what was refused, as
    the sentence's object — "moving this card", "restoring it".

    The card's row is locked first, and what is under it read only once the
    lock is held — see :mod:`app.services.tasks.parentage`. A sub-task being
    written or reopened at the same moment takes the same lock, so one of the
    two waits and then sees what the other committed.

    Raises:
        UnprocessableRequestError: if anything under the task is still open.
    """
    await lock_parent(session, task.id)
    open_cards, open_boxes = await open_work(session, task)
    outstanding = [*open_cards, *open_boxes]
    if not outstanding:
        return

    noun = "sub-task" if len(outstanding) == 1 else "sub-tasks"
    raise UnprocessableRequestError(
        f"{task.reference} still has {len(outstanding)} unfinished {noun}: "
        f"{', '.join(outstanding)}. Finish or cancel each of them before {act} "
        f"to {last_column.name}.",
        details={
            "column": last_column.name,
            "open_subtasks": open_cards,
            "open_checklist_items": open_boxes,
        },
    )


async def open_work(session: AsyncSession, task: Task) -> tuple[list[str], list[str]]:
    """What is still open under a card: its sub-tasks by reference, then its
    checklist items by title.

    Asked by anything putting a card in the board's last column, which a card
    with open work under it may not be in.
    """
    # Read fresh rather than through whatever this session loaded earlier in
    # the request: a sub-task reopened by another request since then is what
    # the caller's lock was taken to see.
    open_cards = [
        subtask.reference
        for subtask in await session.scalars(
            select(Task)
            .where(Task.parent_id == task.id)
            .order_by(Task.sub_number)
            .execution_options(populate_existing=True)
        )
        if not subtask.is_settled
    ]
    open_boxes = [
        item.title
        for item in await session.scalars(
            select(TaskChecklistItem)
            .where(TaskChecklistItem.task_id == task.id)
            .order_by(TaskChecklistItem.position)
            .execution_options(populate_existing=True)
        )
        if item.state is ChecklistState.OPEN
    ]
    return open_cards, open_boxes


def _restart_stages(task: Task, column: BoardColumn) -> None:
    """Start a card's sub-stages again as it arrives in a new column.

    The template's sub-stages for the column replace whatever the card carried;
    a column the template says nothing about keeps the card's own list, and
    only the marker goes back to the first stage.
    """
    stages_from_template = templates.landing_sub_stages(task.template, column.id)
    if stages_from_template is not None:
        task.sub_statuses = stages_from_template
        task.sub_status_index = 0
    elif task.sub_statuses:
        task.sub_status_index = 0


def _mark_finished_by_column(
    task: Task, column: BoardColumn, last_column: BoardColumn
) -> FieldChange | None:
    """Finish a card that has arrived in the last column, or reopen one that left.

    Returns the history entry for the change, or None if the card was already
    as finished as where it now sits says it is.
    """
    was_finished = task.finished_at is not None
    now_finished = column.id == last_column.id
    if now_finished == was_finished:
        return None

    task.finished_at = datetime.now(UTC) if now_finished else None
    return finished_change(was_finished=was_finished, now_finished=now_finished)


def _outcome_label(column: BoardColumn | None, index: int | None) -> str | None:
    """The section a card is in, as its label — or None where it is in none."""
    if column is None or index is None or index >= len(column.outcomes):
        return None
    return column.outcomes[index]


def _landing_outcome(task: Task, column: BoardColumn, wanted: str | None) -> int | None:
    """Which of a column's outcomes a card being moved into it lands on.

    None wherever the column draws no distinction, which is every column but
    the board's last and most of those too. Where it does, an unnamed outcome
    lands on the first the card's template allows: a card dragged into the last
    column has ended, and refusing the drop for want of a word the board could
    supply would make the ordinary gesture the awkward one.

    Raises:
        UnprocessableRequestError: if the column has no outcome by that name,
            or if the card's template does not let its cards end on it.
    """
    if not column.outcomes:
        return None

    if wanted is None:
        allowed = templates.permitted_outcomes(task.template, column)
        return column.outcomes.index(allowed[0]) if allowed else 0

    for index, label in enumerate(column.outcomes):
        if label.casefold() == wanted.casefold():
            templates.require_outcome_permitted(task, column, label)
            return index

    raise UnprocessableRequestError(
        f"{column.name} has no outcome called {wanted!r}. It has {', '.join(column.outcomes)}.",
        details={"column": column.name, "outcome": wanted, "outcomes": list(column.outcomes)},
    )


def _board_column_id(task: Task) -> UUID:
    """The column a top-level card is in.

    States the invariant ``placed_by_parentage`` enforces rather than checking
    it: everything that asks has already refused a sub-task and a card in cold
    storage, which are the only tasks with no column to name.

    Raises:
        UnprocessableRequestError: if a sub-task reaches here anyway, which is a
            bug rather than a request the caller can fix.
    """
    if task.column_id is None:
        raise UnprocessableRequestError(
            f"{task.reference} is a sub-task and is not on the board.",
            details={"reference": task.reference},
        )
    return task.column_id
