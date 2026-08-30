"""Tasks: creating them, moving them, stalling them, talking about them.

Two rules here are worth stating outright, because they are choices rather
than mechanics:

* a new task lands in the first column and nowhere else — work enters a board
  at one end, and letting a client drop a card straight into "Done" makes the
  board a record of intentions rather than of progress;
* a task cannot go on hold or become blocked without a reason — a red card
  that does not say why is a question, not information.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import CommentKind, Task, TaskComment, TaskStatus, TaskWaitingOn
from app.schemas.tasks import CommentCreate, TaskCreate, TaskMove, TaskStatusChange, TaskUpdate
from app.services import columns, projects

_CLEARABLE = frozenset({"jira_ref", "pr_ref"})
"""The only task fields a ``PATCH`` may set back to null."""


async def create(session: AsyncSession, project: Project, data: TaskCreate) -> Task:
    """Add a task to the bottom of the board's first column.

    Raises:
        UnprocessableRequestError: if the assignee is not a project member.
    """
    await projects.require_members(session, project.id, [data.assignee_id])

    column = await columns.first(session, project)
    number = await _next_number(session, project)
    position = await _length(session, column.id)

    task = Task(
        project_id=project.id,
        number=number,
        column_id=column.id,
        position=position,
        title=data.title,
        description=data.description,
        type=data.type,
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
    await session.refresh(task, ["project", "assignee", "waiting_on"])
    return task


async def resolve(session: AsyncSession, reference: str) -> Task:
    """Look a task up by id or by reference.

    Args:
        reference: A UUID, or a task reference such as ``ATL-41``
            (case-insensitive on the key).

    Raises:
        NotFoundError: if nothing matches.
    """
    try:
        statement = select(Task).where(Task.id == UUID(reference))
    except ValueError:
        key, _, number = reference.rpartition("-")
        if not key or not number.isdigit():
            raise NotFoundError(f"No task matching {reference!r}.") from None
        statement = (
            select(Task)
            .join(Project, Project.id == Task.project_id)
            .where(Project.key == key.upper(), Task.number == int(number))
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


async def update(session: AsyncSession, task: Task, data: TaskUpdate) -> Task:
    """Apply a partial update. Status and column move through their own calls.

    Raises:
        UnprocessableRequestError: if a new assignee is not a project member.
    """
    # Only the two external refs can be cleared. A null anywhere else is a
    # client sending back a field it never filled in, not a request to erase a
    # title or unassign the work.
    fields = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in _CLEARABLE
    }

    if "assignee_id" in fields:
        await projects.require_members(session, task.project_id, [fields["assignee_id"]])

    for field, value in fields.items():
        setattr(task, field, value)

    await session.flush()
    await session.refresh(task, ["assignee"])
    return task


async def delete(session: AsyncSession, task: Task) -> None:
    """Remove a task, closing the gap it leaves in its column.

    Its number is not returned to the pool: ``project.task_counter`` only ever
    goes up, so ``ATL-41`` never names a second task.
    """
    column_id = task.column_id
    await session.delete(task)
    await session.flush()
    await _renumber(session, column_id)


async def move(session: AsyncSession, task: Task, data: TaskMove) -> Task:
    """Put a task in a column at a position.

    Unlike creation this is unrestricted: once a card is on the board it may go
    anywhere, including straight back to the first column.

    Raises:
        UnprocessableRequestError: if the column belongs to another project.
    """
    column = await columns.get(session, data.column_id)
    if column.project_id != task.project_id:
        raise UnprocessableRequestError(
            "That column is on a different project's board.",
            details={"column_id": str(column.id)},
        )

    source_id = task.column_id
    siblings = [
        sibling
        for sibling in await _ordered(session, column.id)
        if sibling.id != task.id  # a move within one column must not count twice
    ]
    siblings.insert(min(data.position, len(siblings)), task)

    task.column_id = column.id
    for position, sibling in enumerate(siblings):
        sibling.position = position
    await session.flush()

    if source_id != column.id:
        await _renumber(session, source_id)

    return task


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
