"""A person's plan for a day: picking cards up, listing to-dos, wrapping up.

See :mod:`app.models.today` for what a plan is and why moving a to-do on is a
copy. Everything here is scoped to one person — every lookup takes the
caller's ``person_id`` and a row belonging to anybody else is not found, the
same answer a row that never existed gets.

**A pick follows its card.** What a card holds — its stages, its sub-tasks,
its tick boxes — comes onto the plan as to-dos linked back by ``source``, and
:func:`sync` keeps the two in step: something added to the card later appears
on the plan, and something ticked on the board is ticked here. Ticking a linked
to-do on the plan ticks the thing itself; that half is the router's, because
it is a change to the card and is recorded on the card's history like any
other. Only days from today on follow their cards. A day gone by keeps the
plan it had, which is what its report is a record of.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.errors import (
    ForbiddenError,
    NotFoundError,
    UnprocessableRequestError,
    not_on_this_project,
)
from app.models.project import Project
from app.models.task import ChecklistState, Task, TaskChecklistItem, TaskStatus
from app.models.today import DayPick, Todo, TodoState, pick_loads
from app.schemas.today import PickedCard, PickRead, TodoRead, TodoUpdate
from app.services import tasks


def person_of(principal: Principal) -> UUID:
    """Who the plan belongs to.

    Raises:
        ForbiddenError: for the bootstrap session, which is nobody — a plan for
            the day has to be somebody's.
    """
    if principal.person_id is None:
        raise ForbiddenError("A plan for the day belongs to a person. Sign in as one first.")
    return principal.person_id


async def picks_for(
    session: AsyncSession, project: Project, person_id: UUID, day: date
) -> list[DayPick]:
    """One person's picks on one project for one day, in the order picked."""
    return list(
        await session.scalars(
            select(DayPick)
            .options(*pick_loads())
            .join(Task, Task.id == DayPick.task_id)
            .where(
                DayPick.person_id == person_id,
                DayPick.day == day,
                Task.project_id == project.id,
            )
            .order_by(DayPick.position, DayPick.created_at)
            # A pick already in this session may be holding a to-do list from
            # before a change made in it; read the lists afresh.
            .execution_options(populate_existing=True)
        )
    )


async def pick(
    session: AsyncSession, project: Project, person_id: UUID, task: Task, day: date
) -> DayPick:
    """Put a card on a person's day — or hand back the pick that already does.

    A card freshly picked brings what is still outstanding on it as to-dos —
    see :func:`sync`. Those are the pieces of the card a day spent on it is
    spent on, so they are written down rather than retyped.

    Raises:
        UnprocessableRequestError: for a task on another project, a sub-task, or
            a card in cold storage. A day is planned around cards on the board.
    """
    if task.project_id != project.id:
        raise not_on_this_project("task", task=task.reference)
    if task.parent_id is not None:
        raise UnprocessableRequestError(
            f"{task.reference} is a sub-task. Pick its card, {task.reference.rsplit('-', 1)[0]}, "
            "and list what is to be done about it there."
        )
    if task.column_id is None:
        raise UnprocessableRequestError(
            f"{task.reference} is in cold storage. Take it back onto the board first."
        )
    existing = await _existing_pick(session, person_id, task.id, day)
    if existing is not None:
        return existing

    picked = await _pick_on(session, person_id, task.id, day)
    await sync(session, picked)
    return picked


@dataclass(frozen=True, slots=True)
class CardItem:
    """One thing on a card a to-do can stand for, and where the card says it is."""

    source: str
    text: str
    state: TodoState | None
    """``open``, ``done`` or ``not_done`` (cancelled on the card) — or None where
    the card cannot say, which is only the last stage while it is in hand: a
    card has no stage after its last to move on to, so finishing it is the
    plan's to record."""


_CHECKLIST_STATES = {
    ChecklistState.OPEN: TodoState.OPEN,
    ChecklistState.DONE: TodoState.DONE,
    ChecklistState.CANCELLED: TodoState.NOT_DONE,
}


async def card_items(session: AsyncSession, task: Task) -> list[CardItem]:
    """Everything on a card a to-do can stand for, in the card's own order.

    Stages first: those before the one in hand are done, the one in hand and
    those after it are not — all of them done once the card is finished. A
    stage is named with the column it belongs to, because moving the card
    replaces its stages with the next column's. Then sub-tasks by sub-number,
    named by reference so the line says which; then tick boxes.
    """
    items: list[CardItem] = []
    if task.sub_status_index is not None and task.column_id is not None:
        last = len(task.sub_statuses) - 1
        for index, stage in enumerate(task.sub_statuses):
            state: TodoState | None
            if task.finished_at is not None or index < task.sub_status_index:
                state = TodoState.DONE
            elif index == task.sub_status_index == last:
                state = None
            else:
                state = TodoState.OPEN
            items.append(CardItem(stage_source(task.column_id, index), f"Stage: {stage}", state))
    for sub in await tasks.subtasks(session, task):
        if sub.status is TaskStatus.CANCELLED:
            state = TodoState.NOT_DONE
        else:
            state = TodoState.DONE if sub.finished_at is not None else TodoState.OPEN
        items.append(CardItem(f"subtask:{sub.id}", f"{sub.reference} {sub.title}", state))
    items += [
        CardItem(f"checklist:{item.id}", item.title, _CHECKLIST_STATES[item.state])
        for item in task.checklist
    ]
    return items


async def sync(session: AsyncSession, picked: DayPick) -> None:
    """Bring a pick in step with its card.

    Anything on the card not yet on the plan and still to do is added, unless
    it was deleted from this pick before. Each linked to-do takes the state the
    card gives it, and the wording too, so a renamed sub-task reads as it is
    now — unless somebody reworded the to-do, whose words are then their own.
    A to-do the day has already closed — moved on, or left not done — is left
    as the wrap-up left it.

    A to-do written by hand with exactly an item's words is taken to be that
    item and linked to it, rather than listed twice.
    """
    linked = {todo.source: todo for todo in picked.todos if todo.source}
    by_hand = {
        todo.text: todo
        for todo in picked.todos
        if todo.source is None and todo.state in (TodoState.OPEN, TodoState.DONE)
    }
    for item in await card_items(session, picked.task):
        text = _clean(item.text)
        todo = linked.get(item.source)
        if todo is None and text in by_hand:
            todo = by_hand.pop(text)
            todo.source = item.source
        if todo is None:
            if item.source not in picked.dismissed and item.state in (TodoState.OPEN, None):
                await add_todo(session, picked, text, source=item.source)
            continue
        if todo.state in (TodoState.MOVED, TodoState.NOT_DONE):
            continue
        if item.state is not None and item.state is not todo.state:
            _set_state(todo, item.state)
        if not todo.reworded:
            todo.text = text
    await session.flush()


async def sync_day(session: AsyncSession, project: Project, person_id: UUID, day: date) -> None:
    """:func:`sync` every card on one person's day."""
    for picked in await picks_for(session, project, person_id, day):
        await sync(session, picked)


async def _existing_pick(
    session: AsyncSession, person_id: UUID, task_id: UUID, day: date
) -> DayPick | None:
    found: DayPick | None = await session.scalar(
        select(DayPick)
        .options(*pick_loads())
        .where(DayPick.person_id == person_id, DayPick.task_id == task_id, DayPick.day == day)
    )
    return found


async def _pick_on(session: AsyncSession, person_id: UUID, task_id: UUID, day: date) -> DayPick:
    """The person's pick of this card on this day, made if there is none yet."""
    existing = await _existing_pick(session, person_id, task_id, day)
    if existing is not None:
        return existing

    last = await session.scalar(
        select(func.max(DayPick.position)).where(DayPick.person_id == person_id, DayPick.day == day)
    )
    picked = DayPick(
        person_id=person_id,
        task_id=task_id,
        day=day,
        position=0 if last is None else last + 1,
        todos=[],
    )
    session.add(picked)
    await session.flush()
    return picked


async def own_pick(session: AsyncSession, person_id: UUID, pick_id: UUID) -> DayPick:
    """One of the caller's own picks.

    Raises:
        NotFoundError: if there is no such pick, or it is somebody else's.
    """
    found = await session.get(DayPick, pick_id, options=pick_loads())
    if found is None or found.person_id != person_id:
        raise NotFoundError(f"No pick matching {str(pick_id)!r}.")
    return found


async def own_todo(session: AsyncSession, person_id: UUID, todo_id: UUID) -> Todo:
    """One of the caller's own to-dos.

    Raises:
        NotFoundError: if there is no such to-do, or it is somebody else's.
    """
    found = await session.scalar(
        select(Todo)
        .join(DayPick, DayPick.id == Todo.pick_id)
        .where(Todo.id == todo_id, DayPick.person_id == person_id)
    )
    if found is None:
        raise NotFoundError(f"No to-do matching {str(todo_id)!r}.")
    return found


async def unpick(session: AsyncSession, picked: DayPick) -> None:
    """Take a card off the day, and its to-dos with it."""
    await session.delete(picked)
    await session.flush()


async def add_todo(
    session: AsyncSession,
    picked: DayPick,
    text: str,
    *,
    carried_from: date | None = None,
    source: str | None = None,
    reworded: bool = False,
) -> Todo:
    """Add a to-do at the bottom of a pick's list."""
    last = await session.scalar(select(func.max(Todo.position)).where(Todo.pick_id == picked.id))
    todo = Todo(
        pick_id=picked.id,
        position=0 if last is None else last + 1,
        text=_clean(text),
        state=TodoState.OPEN,
        carried_from=carried_from,
        source=source,
        reworded=reworded,
    )
    session.add(todo)
    await session.flush()
    return todo


def refuse_moved(todo: Todo) -> None:
    """Refuse any change to a to-do already handed on to another day.

    The copy on that day is the live one; this is the record that it moved.
    Called before anything is done about the change, the card's half included.

    Raises:
        UnprocessableRequestError: if the to-do has moved.
    """
    if todo.state is TodoState.MOVED:
        moved_to = todo.moved_to
        when = f"{moved_to:%A} {moved_to.day} {moved_to:%B}" if moved_to else "another day"
        raise UnprocessableRequestError(f"That to-do moved to {when}. Change it there instead.")


async def update_todo(session: AsyncSession, todo: Todo, data: TodoUpdate) -> Todo:
    """Retitle, tick, untick or give up on a to-do.

    Rewording an imported one marks it as reworded, so keeping the plan in
    step with its card leaves the new words alone.

    Raises:
        UnprocessableRequestError: for a to-do already handed on to another
            day — see :func:`refuse_moved`.
    """
    refuse_moved(todo)
    if data.text is not None:
        text = _clean(data.text)
        if todo.source is not None and text != todo.text:
            todo.reworded = True
        todo.text = text
    if data.state is not None:
        _set_state(todo, TodoState(data.state))
    await session.flush()
    return todo


def _set_state(todo: Todo, state: TodoState) -> None:
    todo.state = state
    todo.done_at = datetime.now(UTC) if state is TodoState.DONE else None


async def delete_todo(session: AsyncSession, todo: Todo) -> None:
    """Delete a to-do. One imported from the card is remembered as dismissed,
    so keeping the pick in step with the card does not put it back."""
    if todo.source is not None:
        picked = await session.get(DayPick, todo.pick_id)
        if picked is not None and todo.source not in picked.dismissed:
            picked.dismissed = [*picked.dismissed, todo.source]
    await session.delete(todo)
    await session.flush()


def parse_source(source: str) -> tuple[str, str]:
    """``subtask:<id>`` as ``("subtask", "<id>")``."""
    kind, _, ident = source.partition(":")
    return kind, ident


def stage_source(column_id: UUID, index: int) -> str:
    """The ``source`` of a to-do standing for one stage of a card in one column."""
    return f"stage:{column_id}:{index}"


def parse_stage(ident: str) -> tuple[UUID, int] | None:
    """A stage source's ident as ``(column id, index)`` — None for one that is not."""
    column, _, index = ident.partition(":")
    try:
        return UUID(column), int(index)
    except ValueError:
        return None


async def checklist_item(session: AsyncSession, ident: str) -> TaskChecklistItem | None:
    """The tick box a ``checklist:`` source names, if it is still there."""
    try:
        return await session.get(TaskChecklistItem, UUID(ident))
    except ValueError:
        return None


async def wrap_up(
    session: AsyncSession,
    project: Project,
    person_id: UUID,
    *,
    day: date,
    to_day: date,
    move: list[UUID],
) -> tuple[int, int]:
    """Close a day: hand the chosen open to-dos on, and leave the rest behind.

    Every open to-do on the day ends it either ``moved`` — with a fresh copy on
    ``to_day``, under the same card, which is put on that day if it was not
    already — or ``not_done``. Done ones are left as they are.

    Returns:
        How many moved, and how many were left behind.

    Raises:
        UnprocessableRequestError: if ``to_day`` is not after ``day``, or
            ``move`` names something that is not an open to-do on this day.
    """
    if to_day <= day:
        raise UnprocessableRequestError("To-dos can only be moved on to a later day.")

    picks = await picks_for(session, project, person_id, day)
    open_todos = {
        todo.id: (picked, todo)
        for picked in picks
        for todo in picked.todos
        if todo.state is TodoState.OPEN
    }
    strays = [str(todo_id) for todo_id in move if todo_id not in open_todos]
    if strays:
        raise UnprocessableRequestError(
            "Only open to-dos on the day being wrapped up can be moved.",
            details={"not_open_on_this_day": strays},
        )

    chosen = set(move)
    moved = left = 0
    for todo_id, (picked, todo) in open_todos.items():
        if todo_id in chosen:
            onward = await _pick_on(session, person_id, picked.task_id, to_day)
            # A card already on that day, kept in step with its card, may hold
            # this very item already; then there is nothing to carry.
            already = todo.source is not None and await session.scalar(
                select(Todo.id).where(Todo.pick_id == onward.id, Todo.source == todo.source)
            )
            if not already:
                await add_todo(
                    session,
                    onward,
                    todo.text,
                    carried_from=day,
                    source=todo.source,
                    reworded=todo.reworded,
                )
            todo.state = TodoState.MOVED
            todo.moved_to = to_day
            moved += 1
        else:
            todo.state = TodoState.NOT_DONE
            left += 1
    await session.flush()
    return moved, left


def _clean(text: str) -> str:
    """One line, trimmed: a to-do is a line of work."""
    cleaned = " ".join(text.split())
    if not cleaned:
        raise UnprocessableRequestError("A to-do needs some words in it.")
    return cleaned


def todo_read(todo: Todo) -> TodoRead:
    kind = parse_source(todo.source)[0] if todo.source else None
    return TodoRead(
        id=todo.id,
        text=todo.text,
        state=todo.state,
        done_at=todo.done_at,
        moved_to=todo.moved_to,
        carried_from=todo.carried_from,
        source_kind=kind if kind in ("stage", "subtask", "checklist") else None,
        created_at=todo.created_at,
    )


def pick_read(picked: DayPick) -> PickRead:
    task = picked.task
    return PickRead(
        id=picked.id,
        day=picked.day,
        task=PickedCard(
            id=task.id,
            reference=task.reference,
            title=task.title,
            column=task.column.name if task.column else None,
        ),
        todos=[todo_read(todo) for todo in picked.todos],
    )
