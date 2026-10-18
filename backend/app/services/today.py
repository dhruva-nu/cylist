"""A person's plan for a day: picking cards up, listing to-dos, wrapping up.

See :mod:`app.models.today` for what a plan is and why moving a to-do on is a
copy. Everything here is scoped to one person — every lookup takes the
caller's ``person_id`` and a row belonging to anybody else is not found, the
same answer a row that never existed gets.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.errors import ForbiddenError, NotFoundError, UnprocessableRequestError
from app.models.project import Project
from app.models.task import ChecklistState, Task, TaskStatus
from app.models.today import DayPick, Todo, TodoState
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

    A card freshly picked brings what is still outstanding on it as to-dos:
    the stages it has yet to get through, from the one in hand onward, then
    its open sub-tasks and tick boxes. Those are the pieces of the card a day
    spent on it is spent on, so they are written down rather than retyped.
    Nothing is imported into a pick that already existed, nor into one a
    wrap-up makes — that day's to-dos are the ones carried over.

    Raises:
        UnprocessableRequestError: for a task on another project, a sub-task, or
            a card in cold storage. A day is planned around cards on the board.
    """
    if task.project_id != project.id:
        raise UnprocessableRequestError(f"{task.reference} is not on {project.key}.")
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
    for text in await outstanding(session, task):
        await add_todo(session, picked, text)
    return picked


async def outstanding(session: AsyncSession, task: Task) -> list[str]:
    """What is still to be done on a card, one line each, in the card's own order.

    Stages first, from the one in hand to the last — the one in hand is not
    finished, it is what is being worked on. Then sub-tasks neither finished nor
    cancelled, by sub-number, named by reference so the line says which; then
    open tick boxes.
    """
    lines: list[str] = []
    if task.sub_status_index is not None:
        lines += [f"Stage: {stage}" for stage in task.sub_statuses[task.sub_status_index :]]
    lines += [
        f"{sub.reference} {sub.title}"
        for sub in await tasks.subtasks(session, task)
        if sub.finished_at is None and sub.status is not TaskStatus.CANCELLED
    ]
    lines += [item.title for item in task.checklist if item.state is ChecklistState.OPEN]
    return lines


async def _existing_pick(
    session: AsyncSession, person_id: UUID, task_id: UUID, day: date
) -> DayPick | None:
    found: DayPick | None = await session.scalar(
        select(DayPick).where(
            DayPick.person_id == person_id, DayPick.task_id == task_id, DayPick.day == day
        )
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
    found = await session.get(DayPick, pick_id)
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
    session: AsyncSession, picked: DayPick, text: str, *, carried_from: date | None = None
) -> Todo:
    """Add a to-do at the bottom of a pick's list."""
    last = await session.scalar(select(func.max(Todo.position)).where(Todo.pick_id == picked.id))
    todo = Todo(
        pick_id=picked.id,
        position=0 if last is None else last + 1,
        text=_clean(text),
        state=TodoState.OPEN,
        carried_from=carried_from,
    )
    session.add(todo)
    await session.flush()
    return todo


async def update_todo(session: AsyncSession, todo: Todo, data: TodoUpdate) -> Todo:
    """Retitle, tick, untick or give up on a to-do.

    Raises:
        UnprocessableRequestError: for a to-do already handed on to another day.
            The copy on that day is the live one; this is the record that it
            moved.
    """
    if todo.state is TodoState.MOVED:
        moved_to = todo.moved_to
        when = f"{moved_to:%A} {moved_to.day} {moved_to:%B}" if moved_to else "another day"
        raise UnprocessableRequestError(f"That to-do moved to {when}. Change it there instead.")
    if data.text is not None:
        todo.text = _clean(data.text)
    if data.state is not None:
        todo.state = TodoState(data.state)
        todo.done_at = datetime.now(UTC) if todo.state is TodoState.DONE else None
    await session.flush()
    return todo


async def delete_todo(session: AsyncSession, todo: Todo) -> None:
    await session.delete(todo)
    await session.flush()


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
            await add_todo(session, onward, todo.text, carried_from=day)
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
    return TodoRead.model_validate(todo)


async def missing(session: AsyncSession, picked: DayPick) -> list[str]:
    """What is outstanding on a pick's card that its to-dos do not list yet.

    A card picked before it was split further, or before picking imported
    anything at all, still says what it is missing — so the plan can offer to
    bring it in rather than leave the day short of work it plainly holds. A
    line counts as listed whatever state its to-do is in: a stage ticked off
    an hour ago is not missing.
    """
    listed = {todo.text for todo in picked.todos}
    return [line for line in await outstanding(session, picked.task) if _clean(line) not in listed]


async def bring_in(session: AsyncSession, picked: DayPick) -> None:
    """Add whatever :func:`missing` names to the bottom of the pick's list."""
    for line in await missing(session, picked):
        await add_todo(session, picked, line)


async def pick_view(session: AsyncSession, picked: DayPick) -> PickRead:
    """A pick as the API sends it, with what its card holds that it does not."""
    return pick_read(picked, await missing(session, picked))


def pick_read(picked: DayPick, missing_lines: list[str]) -> PickRead:
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
        missing=missing_lines,
    )
