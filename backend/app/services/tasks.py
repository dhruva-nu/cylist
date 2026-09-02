"""Tasks: creating them, moving them, stalling them, talking about them.

Two rules here are worth stating outright, because they are choices rather
than mechanics:

* a new task lands in the first column and nowhere else — work enters a board
  at one end, and letting a client drop a card straight into "Done" makes the
  board a record of intentions rather than of progress;
* a task cannot go on hold or become blocked without a reason — a red card
  that does not say why is a question, not information;
* a card with unfinished sub-tasks cannot reach the board's last column —
  see :func:`unsettled`. A ticket whose parts are still open is not done, and
  the board saying otherwise is how work gets forgotten rather than finished.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.person import Person
from app.models.project import Project
from app.models.task import (
    ChecklistState,
    CommentKind,
    Task,
    TaskChecklistItem,
    TaskComment,
    TaskStatus,
    TaskWaitingOn,
)
from app.schemas.tasks import (
    ChecklistItemCreate,
    ChecklistItemUpdate,
    CommentCreate,
    TaskCreate,
    TaskMove,
    TaskStatusChange,
    TaskUpdate,
)
from app.services import columns, projects

_CLEARABLE = frozenset({"jira_ref", "pr_ref", "due_date"})
"""The only task fields a ``PATCH`` may set back to null.

Everything else reads a null as a client echoing back a field it never filled
in. These three are the fields a card can genuinely be without, so for them a
null is the request it looks like: take the date off, drop the link."""

_TRACKED: dict[str, str] = {
    "title": "title",
    "description": "description",
    "type": "type",
    "priority": "priority",
    "sub_statuses": "sub-statuses",
    "sub_status_index": "sub-status",
    "due_date": "due date",
    "assignee_id": "assignee",
    "jira_ref": "Jira reference",
    "pr_ref": "pull request",
}
"""The fields a task's history reports on, and how it names them.

Insertion order is the order changes are listed in, so an entry reads down the
card the way the card itself does rather than in whatever order the client
happened to send the fields.
"""


async def create(
    session: AsyncSession,
    project: Project,
    data: TaskCreate,
    *,
    parent: Task | None = None,
) -> Task:
    """Add a task to the bottom of the board's first column.

    With ``parent`` the new card is a sub-task of it, numbered ``ATL-41-2``
    rather than taking a project number of its own.

    Raises:
        UnprocessableRequestError: if the assignee is not a project member, or
            if ``parent`` is itself a sub-task.
    """
    await projects.require_members(session, project.id, [data.assignee_id])

    if parent is not None and parent.parent_id is not None:
        raise UnprocessableRequestError(
            f"{parent.reference} is already a sub-task, so it cannot have sub-tasks of its own. "
            "A board that nests further is a tree, not a board.",
            details={"parent": parent.reference},
        )

    column = await columns.first(session, project)
    number = None if parent else await _next_number(session, project)
    sub_number = await _next_sub_number(session, parent) if parent else None
    position = await _length(session, column.id)

    task = Task(
        project_id=project.id,
        number=number,
        parent_id=parent.id if parent else None,
        sub_number=sub_number,
        column_id=column.id,
        position=position,
        title=data.title,
        description=data.description,
        type=data.type,
        priority=data.priority,
        sub_statuses=data.sub_statuses,
        sub_status_index=0 if data.sub_statuses else None,
        due_date=data.due_date,
        assignee_id=data.assignee_id,
        status=TaskStatus.ACTIVE,
        jira_ref=data.jira_ref,
        pr_ref=data.pr_ref,
    )
    session.add(task)
    await session.flush()

    # Relationships are only populated by a SELECT, and a just-inserted row has
    # not had one. Load them now so callers can read them without lazy IO.
    await session.refresh(task, ["project", "assignee", "waiting_on", "parent", "checklist"])
    return task


async def resolve(session: AsyncSession, reference: str) -> Task:
    """Look a task up by id or by reference.

    Args:
        reference: A UUID, or a task reference such as ``ATL-41`` — or
            ``ATL-41-2`` for a sub-task (case-insensitive on the key).

    Raises:
        NotFoundError: if nothing matches.
    """
    try:
        statement = select(Task).where(Task.id == UUID(reference))
    except ValueError:
        # A key can hold no dash, so the parts are unambiguous: ``KEY-number``
        # names a card and ``KEY-number-sub`` names a sub-task of one.
        key, *numbers = reference.split("-")
        if not key or not numbers or not all(part.isdigit() for part in numbers):
            raise NotFoundError(f"No task matching {reference!r}.") from None
        if len(numbers) > 2:
            raise NotFoundError(
                f"No task matching {reference!r}. Sub-tasks go one level deep, "
                "so a reference carries at most two numbers."
            ) from None

        statement = (
            select(Task)
            .join(Project, Project.id == Task.project_id)
            .where(Project.key == key.upper(), Task.number == int(numbers[0]))
        )
        if len(numbers) == 2:
            parent = statement.subquery()
            statement = select(Task).where(
                Task.parent_id.in_(select(parent.c.id)),
                Task.sub_number == int(numbers[1]),
            )

    task = await session.scalar(statement)
    if task is None:
        raise NotFoundError(f"No task matching {reference!r}.")
    return task


async def list_for_project(session: AsyncSession, project: Project) -> list[Task]:
    """Return the whole board's cards, in the order they are stacked."""
    return list(
        await session.scalars(
            select(Task)
            .join(BoardColumn, BoardColumn.id == Task.column_id)
            .where(Task.project_id == project.id)
            .order_by(BoardColumn.position, Task.position)
        )
    )


async def update(
    session: AsyncSession, task: Task, data: TaskUpdate
) -> tuple[Task, list[dict[str, Any]]]:
    """Apply a partial update. Status and column move through their own calls.

    Returns the task and what actually changed about it — field by field, old
    value beside new — which is what the card's history is written from. The
    diff is taken from the row rather than from the request because a ``PATCH``
    routinely sends back fields it is not changing, and a history that says
    "changed the title" every time somebody edits a due date is one nobody
    reads. It is taken *after* the write for the same reason: setting the
    stages moves the current-stage marker, and that move is a change to report
    even though no client asked for it.

    Raises:
        UnprocessableRequestError: if a new assignee is not a project member.
    """
    before = _snapshot(task)

    # Only the fields in _CLEARABLE can be emptied. A null anywhere else is a
    # client sending back a field it never filled in, not a request to erase a
    # title or unassign the work.
    fields = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in _CLEARABLE
    }

    if "assignee_id" in fields:
        await projects.require_members(session, task.project_id, [fields["assignee_id"]])

    if "sub_statuses" in fields or "sub_status_index" in fields:
        new_statuses = fields.pop("sub_statuses", task.sub_statuses)
        wanted = fields.pop("sub_status_index", None)
        if not new_statuses:
            # Nothing to point at, so an index sent alongside is moot rather
            # than wrong: clearing the stages clears the marker.
            task.sub_status_index = None
        elif wanted is not None:
            # A caller that reordered or removed a stage knows where the
            # current one ended up. Out of range is a bug on its side, not
            # something to quietly round off.
            if wanted >= len(new_statuses):
                raise UnprocessableRequestError(
                    f"sub_status_index {wanted} is past the last of "
                    f"{len(new_statuses)} sub-statuses.",
                )
            task.sub_status_index = wanted
        else:
            # Preserve how far along the card was rather than restarting it —
            # only pulled back as far as the shorter list requires.
            task.sub_status_index = min(task.sub_status_index or 0, len(new_statuses) - 1)
        task.sub_statuses = new_statuses

    for field, value in fields.items():
        setattr(task, field, value)

    await session.flush()
    await session.refresh(task, ["assignee"])
    return task, await _diff(session, before, _snapshot(task))


async def set_sub_status(
    session: AsyncSession, task: Task, index: int
) -> tuple[Task, list[dict[str, Any]]]:
    """Move a task to one of its sub-status stages.

    Any stage, in either direction: the board draws this as a slider, and a
    slider that only went forwards would have no way to undo a mis-click. The
    index is taken as given rather than clamped — the caller is pointing at a
    stage it can see, so one out of range means it is looking at a stale list
    and should be told so.

    Raises:
        UnprocessableRequestError: if the task has no sub-statuses set, or the
            index does not name one of them.
    """
    if not task.sub_statuses:
        raise UnprocessableRequestError(
            f"{task.reference} has no sub-statuses set. Add some before moving between them.",
        )
    if not 0 <= index < len(task.sub_statuses):
        raise UnprocessableRequestError(
            f"{task.reference} has {len(task.sub_statuses)} sub-statuses; "
            f"there is no stage {index}.",
        )
    was_on = _stage(task.sub_statuses, task.sub_status_index)
    task.sub_status_index = index
    await session.flush()

    now_on = _stage(task.sub_statuses, index)
    changes: list[dict[str, Any]] = (
        []
        if now_on == was_on
        else [{"field": "sub_status_index", "label": "sub-status", "from": was_on, "to": now_on}]
    )
    return task, changes


async def delete(session: AsyncSession, task: Task) -> None:
    """Remove a task, closing the gap it leaves in its column.

    Its number is not returned to the pool: ``project.task_counter`` only ever
    goes up, so ``ATL-41`` never names a second task.
    """
    # A parent takes its sub-tasks with it (ON DELETE CASCADE), and those may
    # sit in other columns, so every column the deletion empties a slot in has
    # to close up — not just the one the card itself was in.
    emptied = {task.column_id} | set(
        await session.scalars(select(Task.column_id).where(Task.parent_id == task.id))
    )
    await session.delete(task)
    await session.flush()
    for column_id in emptied:
        await _renumber(session, column_id)


async def move(
    session: AsyncSession, task: Task, data: TaskMove
) -> tuple[Task, list[dict[str, Any]]]:
    """Put a task in a column at a position.

    Unlike creation this is unrestricted: once a card is on the board it may go
    anywhere, including straight back to the first column.

    A card that changes column starts its stages again. ``sub_statuses`` is
    progress through the column the card is in, not through the board — "drafted,
    reviewed, merged" means one thing in Review and another in Done — so a card
    arriving somewhere new has not begun the stages it keeps there. Moving
    within one column leaves the stage alone: nothing has been arrived at.

    Returns the task and what changed about it, in the same shape
    :func:`update` reports — the column it left and the one it arrived in, and
    the stage it was put back to if that happened. Reordering within one column
    changes nothing worth recording: a card's place in a stack is not a fact
    about the work.

    Raises:
        UnprocessableRequestError: if the column belongs to another project, or
            if the destination is the last column and a sub-task is still open.
    """
    column = await columns.get(session, data.column_id)
    if column.project_id != task.project_id:
        raise UnprocessableRequestError(
            "That column is on a different project's board.",
            details={"column_id": str(column.id)},
        )

    await _refuse_unfinished(session, task, column)

    source_id = task.column_id
    was_on = _stage(task.sub_statuses, task.sub_status_index)
    siblings = [
        sibling
        for sibling in await _ordered(session, column.id)
        if sibling.id != task.id  # a move within one column must not count twice
    ]
    siblings.insert(min(data.position, len(siblings)), task)

    task.column_id = column.id
    if source_id != column.id and task.sub_statuses:
        task.sub_status_index = 0
    for position, sibling in enumerate(siblings):
        sibling.position = position
    await session.flush()

    if source_id == column.id:
        return task, []

    await _renumber(session, source_id)
    source = await columns.get(session, source_id)
    changes: list[dict[str, Any]] = [
        {"field": "column", "label": "column", "from": source.name, "to": column.name},
    ]
    now_on = _stage(task.sub_statuses, task.sub_status_index)
    if now_on != was_on:
        changes.append(
            {"field": "sub_status_index", "label": "sub-status", "from": was_on, "to": now_on}
        )
    return task, changes


async def change_status(
    session: AsyncSession, task: Task, data: TaskStatusChange
) -> tuple[Task, TaskComment]:
    """Move a task between active, on hold and blocked.

    Writes the change to the task's timeline and replaces the set of people it
    is waiting on. Going back to active clears those tags: the question "who is
    holding this up" has no answer once nothing is.

    Raises:
        UnprocessableRequestError: if a stalling status arrives without a
            reason, or if a tagged person is not a project member.
    """
    reason = (data.reason or "").strip()
    if data.status is not TaskStatus.ACTIVE and not reason:
        raise UnprocessableRequestError(
            f"Say why this task is {data.status.label.lower()}.",
            details={"field": "reason"},
        )

    tagged = [] if data.status is TaskStatus.ACTIVE else data.waiting_on
    await projects.require_members(session, task.project_id, tagged)

    previous = task.status
    task.status = data.status

    await session.execute(sql_delete(TaskWaitingOn).where(TaskWaitingOn.task_id == task.id))
    session.add_all(TaskWaitingOn(task_id=task.id, person_id=person_id) for person_id in tagged)

    entry = TaskComment(
        task_id=task.id,
        author_id=None,
        body=f"{data.status.label} — {reason}" if reason else data.status.label,
        kind=CommentKind.STATUS_CHANGE,
        meta={
            "from": previous.value,
            "to": data.status.value,
            "reason": reason,
            "tagged": [str(person_id) for person_id in tagged],
        },
    )
    session.add(entry)
    await session.flush()

    # The membership was rewritten underneath the relationship; reload it so
    # the response says who the task is waiting on now, not a moment ago.
    await session.refresh(task, ["waiting_on"])
    return task, entry


async def comments(session: AsyncSession, task: Task) -> list[TaskComment]:
    """The task's timeline, oldest first — typed comments and status changes."""
    return list(
        await session.scalars(
            select(TaskComment)
            .where(TaskComment.task_id == task.id)
            .order_by(TaskComment.created_at, TaskComment.id)
        )
    )


async def comment(session: AsyncSession, task: Task, data: CommentCreate) -> TaskComment:
    """Add a comment to a task.

    Raises:
        UnprocessableRequestError: if the named author is not a project member.
    """
    if data.author_id is not None:
        await projects.require_members(session, task.project_id, [data.author_id])

    entry = TaskComment(
        task_id=task.id,
        author_id=data.author_id,
        body=data.body,
        kind=CommentKind.COMMENT,
        meta={},
    )
    session.add(entry)
    await session.flush()
    await session.refresh(entry, ["author"])
    return entry


async def comment_counts(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, int]:
    """How long each task's timeline is, in one query.

    Counted rather than loaded: the board shows a number, and fetching every
    comment body to render it would be a lot of rows for one glyph.
    """
    if not task_ids:
        return {}

    rows = await session.execute(
        select(TaskComment.task_id, func.count())
        .where(TaskComment.task_id.in_(task_ids))
        .group_by(TaskComment.task_id)
    )
    return dict(rows.tuples().all())


async def status_counts(session: AsyncSession, project: Project) -> dict[TaskStatus, int]:
    """Count a project's tasks by status in one query — the hub's headline."""
    rows = await session.execute(
        select(Task.status, func.count()).where(Task.project_id == project.id).group_by(Task.status)
    )
    counts = dict.fromkeys(TaskStatus, 0)
    for status, total in rows:
        counts[status] = total
    return counts


async def subtasks(session: AsyncSession, task: Task) -> list[Task]:
    """The task's own cards on the board, in sub-number order."""
    return list(
        await session.scalars(
            select(Task).where(Task.parent_id == task.id).order_by(Task.sub_number)
        )
    )


async def checklist(session: AsyncSession, task: Task) -> list[TaskChecklistItem]:
    """The task's tick boxes, top to bottom."""
    return list(
        await session.scalars(
            select(TaskChecklistItem)
            .where(TaskChecklistItem.task_id == task.id)
            .order_by(TaskChecklistItem.position)
        )
    )


async def add_checklist_item(
    session: AsyncSession, task: Task, data: ChecklistItemCreate
) -> TaskChecklistItem:
    """Add a tick box to the bottom of a task's checklist."""
    existing = await session.scalar(
        select(func.count())
        .select_from(TaskChecklistItem)
        .where(TaskChecklistItem.task_id == task.id)
    )
    item = TaskChecklistItem(
        task_id=task.id,
        title=data.title,
        state=ChecklistState.OPEN,
        position=existing or 0,
    )
    session.add(item)
    await session.flush()
    return item


async def get_checklist_item(session: AsyncSession, item_id: UUID) -> TaskChecklistItem:
    item = await session.get(TaskChecklistItem, item_id)
    if item is None:
        raise NotFoundError("No checklist item with that id.")
    return item


async def update_checklist_item(
    session: AsyncSession, item: TaskChecklistItem, data: ChecklistItemUpdate
) -> TaskChecklistItem:
    """Retitle an item, or tick, cancel or reopen it."""
    if data.title is not None:
        item.title = data.title
    if data.state is not None:
        item.state = data.state
    await session.flush()
    return item


async def delete_checklist_item(session: AsyncSession, item: TaskChecklistItem) -> None:
    """Remove a tick box, closing the gap it leaves."""
    task_id = item.task_id
    await session.delete(item)
    await session.flush()
    for position, remaining in enumerate(
        await session.scalars(
            select(TaskChecklistItem)
            .where(TaskChecklistItem.task_id == task_id)
            .order_by(TaskChecklistItem.position)
        )
    ):
        remaining.position = position
    await session.flush()


async def open_subtask_counts(
    session: AsyncSession, project_id: UUID, task_ids: list[UUID]
) -> dict[UUID, int]:
    """How many sub-tasks — cards and tick boxes alike — are still open.

    Two queries for the whole board rather than a walk per card: this number
    appears on every task in every listing, and the rule it stands for is the
    one that decides whether a card may be dropped in the last column.
    """
    if not task_ids:
        return {}

    finished = (await columns.last(session, project_id)).id
    counts: dict[UUID, int] = {}

    open_cards = await session.execute(
        select(Task.parent_id, func.count())
        .where(
            Task.parent_id.in_(task_ids),
            Task.status != TaskStatus.CANCELLED,
            Task.column_id != finished,
        )
        .group_by(Task.parent_id)
    )
    for parent_id, total in open_cards:
        counts[parent_id] = counts.get(parent_id, 0) + total

    open_boxes = await session.execute(
        select(TaskChecklistItem.task_id, func.count())
        .where(
            TaskChecklistItem.task_id.in_(task_ids),
            TaskChecklistItem.state == ChecklistState.OPEN,
        )
        .group_by(TaskChecklistItem.task_id)
    )
    for task_id, total in open_boxes:
        counts[task_id] = counts.get(task_id, 0) + total

    return counts


def _snapshot(task: Task) -> dict[str, Any]:
    """The tracked fields of a task as they stand, for diffing against later."""
    return {field: getattr(task, field) for field in _TRACKED}


async def _diff(
    session: AsyncSession, before: dict[str, Any], after: dict[str, Any]
) -> list[dict[str, Any]]:
    """Describe what moved between two snapshots, in :data:`_TRACKED` order.

    Values are rendered the way the card renders them — an assignee by name, a
    sub-status by its label — because the history is read by people, and an id
    tells the reader nothing about who picked the work up.
    """
    changes: list[dict[str, Any]] = []
    for field, label in _TRACKED.items():
        was, now = before[field], after[field]
        if was == now:
            continue
        if field == "assignee_id":
            # Reported as `assignee`, because what is reported is a person's
            # name: calling the field `assignee_id` beside a name would be a
            # lie about what the value is.
            field = "assignee"
            was, now = await _person_name(session, was), await _person_name(session, now)
        elif field == "sub_status_index":
            was = _stage(before["sub_statuses"], was)
            now = _stage(after["sub_statuses"], now)
        changes.append({"field": field, "label": label, "from": _plain(was), "to": _plain(now)})
    return changes


def _stage(labels: list[str], index: int | None) -> str | None:
    """Name the stage an index points at, for a card that has stages."""
    if index is None or not 0 <= index < len(labels):
        return None
    return labels[index]


async def _person_name(session: AsyncSession, person_id: UUID | None) -> str | None:
    if person_id is None:
        return None
    person = await session.get(Person, person_id)
    return person.name if person else None


def _plain(value: Any) -> Any:
    """Make a value fit to store in the audit payload, which is JSONB."""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


async def _refuse_unfinished(session: AsyncSession, task: Task, column: BoardColumn) -> None:
    """Stop a card reaching the last column while a sub-task is still open.

    Enforced on the move rather than on the sub-task, because "done" is a place
    on the board rather than a flag: the only moment the rule can be checked is
    the moment the card is put there.

    Raises:
        UnprocessableRequestError: if anything under the task is still open.
    """
    finished = await columns.last(session, task.project_id)
    if column.id != finished.id or task.column_id == finished.id:
        return

    open_cards = [
        subtask.reference
        for subtask in await subtasks(session, task)
        if subtask.status is not TaskStatus.CANCELLED and subtask.column_id != finished.id
    ]
    open_boxes = [
        item.title for item in await checklist(session, task) if item.state is ChecklistState.OPEN
    ]
    if not open_cards and not open_boxes:
        return

    outstanding = [*open_cards, *open_boxes]
    raise UnprocessableRequestError(
        f"{task.reference} still has {len(outstanding)} unfinished "
        f"{'sub-task' if len(outstanding) == 1 else 'sub-tasks'}: "
        f"{', '.join(outstanding)}. Finish or cancel each of them before moving this card "
        f"to {finished.name}.",
        details={
            "column": finished.name,
            "open_subtasks": open_cards,
            "open_checklist_items": open_boxes,
        },
    )


async def _next_sub_number(session: AsyncSession, parent: Task) -> int:
    """Take the next sub-number under a card.

    The parent's own counter, locked, for the same reason a project has one:
    ``ATL-41-2`` must never name a second sub-task, however many were deleted
    or created at the same moment.
    """
    counter = await session.scalar(
        select(Task.subtask_counter).where(Task.id == parent.id).with_for_update()
    )
    parent.subtask_counter = (counter or 0) + 1
    await session.flush()
    return parent.subtask_counter


async def _next_number(session: AsyncSession, project: Project) -> int:
    """Take the next task number for a project.

    Locks the project row instead of taking ``MAX(number) + 1``: a deleted
    task's number must never come back, and two callers creating a task at the
    same moment must not be handed the same one.
    """
    counter = await session.scalar(
        select(Project.task_counter).where(Project.id == project.id).with_for_update()
    )
    project.task_counter = (counter or 0) + 1
    await session.flush()
    return project.task_counter


async def _ordered(session: AsyncSession, column_id: UUID) -> list[Task]:
    return list(
        await session.scalars(
            select(Task).where(Task.column_id == column_id).order_by(Task.position)
        )
    )


async def _length(session: AsyncSession, column_id: UUID) -> int:
    total = await session.scalar(
        select(func.count()).select_from(Task).where(Task.column_id == column_id)
    )
    return total or 0


async def _renumber(session: AsyncSession, column_id: UUID) -> None:
    """Close the gap a departed task left, keeping positions contiguous."""
    for position, task in enumerate(await _ordered(session, column_id)):
        task.position = position
    await session.flush()
