"""Tasks: creating them, moving them, stalling them, talking about them.

Two rules here are worth stating outright, because they are choices rather
than mechanics:

* a new task lands in the first column and nowhere else — work enters a board
  at one end, and letting a client drop a card straight into "Done" makes the
  board a record of intentions rather than of progress;
* a card created from a template goes only where that template's stages
  allow it — see :mod:`app.services.templates`, which owns that rule and is
  asked about it here twice: once to decide where a new card lands, and once
  on every move;
* a card lands on the sub-stages its template set for the column it is in —
  its own ``sub_statuses``, reloaded from the template every time the card
  arrives somewhere new — and cannot leave a column while it is short of the
  last one; see :func:`_refuse_stage_incomplete`. This is checked on every
  move out of the column, not only into the board's last one;
* a task cannot go on hold or become blocked without a reason — a red card
  that does not say why is a question, not information;
* a card with unfinished sub-tasks cannot reach the board's last column —
  see :func:`_refuse_unfinished`. A ticket whose parts are still open is not
  done, and the board saying otherwise is how work gets forgotten rather than
  finished;
* a sub-task is not on the board. It has no column, it cannot be moved, and it
  is finished by :func:`set_finished` rather than by being dragged somewhere —
  one piece of work is one card, and a card dealt out across four columns is
  four things to read where there was one thing to do;
* a goal is set on a card, not on a sub-task — see
  :func:`_refuse_goal_on_subtask`. A sub-task belongs to its card, and its card
  is what belongs to the goal.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any, NamedTuple
from uuid import UUID

from sqlalchemy import delete as sql_delete
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.goal import Goal
from app.models.person import Person
from app.models.project import Project
from app.models.task import (
    ChecklistState,
    CommentKind,
    Task,
    TaskChecklistItem,
    TaskColumnDueDate,
    TaskComment,
    TaskStatus,
    TaskWaitingOn,
)
from app.models.template import TaskTemplate
from app.schemas.tasks import (
    ChecklistItemCreate,
    ChecklistItemUpdate,
    ColumnDueDateInput,
    CommentCreate,
    TaskCreate,
    TaskMove,
    TaskStatusChange,
    TaskUpdate,
)
from app.services import columns, goals, projects, templates


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


class Split(NamedTuple):
    """How far a card is through the things it was split into.

    Read as "``open`` of ``total`` left" — or, on a card, as the bar drawn from
    the two. Both halves count sub-tasks and tick boxes together, and neither
    counts anything cancelled.
    """

    total: int
    open: int


_CLEARABLE = frozenset({"jira_ref", "pr_ref", "due_date", "template_id", "goal_id"})
"""The only task fields a ``PATCH`` may set back to null.

Everything else reads a null as a client echoing back a field it never filled
in. These five are the fields a card can genuinely be without, so for them a
null is the request it looks like: take the date off, drop the link, take the
card out of its template, take it off its goal."""

_TRACKED: dict[str, str] = {
    "title": "title",
    "description": "description",
    "type": "type",
    "priority": "priority",
    "sub_statuses": "sub-statuses",
    "sub_status_index": "sub-status",
    "due_date": "due date",
    "assignee_id": "assignee",
    "template_id": "template",
    "goal_id": "goal",
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

    The column is the first one *this card* may land in: the board's first,
    unless the card's template is barred from it, in which case the leftmost
    column that template does allow. A card has to be born somewhere its own
    template permits.

    With ``parent`` the new task is a sub-task of it, numbered ``ATL-41-2``
    rather than taking a project number of its own — and it lands in no column
    at all, because a sub-task is work on a card rather than a card on the
    board. A template's stages are rules about columns, so they have nothing to
    say about a sub-task; it may still name one, because what kind of work it is
    stays true whether or not the board is arranging it.

    Raises:
        UnprocessableRequestError: if the assignee is not a project member, if
            the template or the goal belongs to another project, if a sub-task
            is given a goal of its own, or if ``parent`` is itself a sub-task.
    """
    await projects.require_members(session, project.id, [data.assignee_id])
    template = await templates.require_template(session, project.id, data.template_id)
    await goals.require_goal(session, project.id, data.goal_id)

    if parent is not None and data.goal_id is not None:
        raise UnprocessableRequestError(
            f"A sub-task cannot be put on a goal of its own. {parent.reference} is what "
            "belongs to a goal; this is work on it.",
            details={"parent": parent.reference, "goal_id": str(data.goal_id)},
        )

    if parent is not None and parent.parent_id is not None:
        raise UnprocessableRequestError(
            f"{parent.reference} is already a sub-task, so it cannot have sub-tasks of its own. "
            "A board that nests further is a tree, not a board.",
            details={"parent": parent.reference},
        )

    number = None if parent else await _next_number(session, project)
    sub_number = await _next_sub_number(session, parent) if parent else None

    column = (
        None
        if parent
        else templates.landing_column(template, await columns.first(session, project))
    )
    position = None if column is None else await _length(session, column.id)

    # The template's own sub-stages for the landing column win over whatever
    # the caller sent — that is the template's whole point — but a column the
    # template says nothing about leaves the caller's choice alone. A sub-task
    # lands in no column, so there is no stage to inherit and the caller's own
    # list stands.
    sub_statuses = None if column is None else templates.landing_sub_stages(template, column.id)
    if sub_statuses is None:
        sub_statuses = data.sub_statuses

    task = Task(
        project_id=project.id,
        number=number,
        parent_id=parent.id if parent else None,
        sub_number=sub_number,
        column_id=column.id if column else None,
        position=position,
        title=data.title,
        description=data.description,
        type=data.type,
        priority=data.priority,
        sub_statuses=sub_statuses,
        sub_status_index=0 if sub_statuses else None,
        due_date=data.due_date,
        assignee_id=data.assignee_id,
        template_id=data.template_id,
        goal_id=data.goal_id,
        status=TaskStatus.ACTIVE,
        jira_ref=data.jira_ref,
        pr_ref=data.pr_ref,
    )
    session.add(task)
    await session.flush()

    # Relationships are only populated by a SELECT, and a just-inserted row has
    # not had one. Load them now so callers can read them without lazy IO.
    await session.refresh(
        task,
        [
            "project",
            "assignee",
            "template",
            "goal",
            "waiting_on",
            "parent",
            "checklist",
            "column_due_dates",
        ],
    )
    await set_column_due_dates(session, task, data.column_due_dates)
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
    """Return the board's cards, in the order they are stacked.

    Top-level cards alone: a sub-task is not on the board, and arrives with the
    card it belongs to instead — see :func:`subtasks`. Anything that wants every
    row on the project, sub-tasks included, wants :func:`all_for_project`.
    """
    return list(
        await session.scalars(
            select(Task)
            .join(BoardColumn, BoardColumn.id == Task.column_id)
            .where(Task.project_id == project.id, Task.parent_id.is_(None))
            .order_by(BoardColumn.position, Task.position)
        )
    )


async def all_for_project(session: AsyncSession, project: Project) -> list[Task]:
    """Every task on the project, sub-tasks among them, oldest first.

    For the reader that is not drawing a board: a day's report looks a card up
    by the reference an activity entry names, and ``ATL-41-2`` is as likely to
    be in a day's work as ``ATL-41`` is.
    """
    return list(
        await session.scalars(
            select(Task).where(Task.project_id == project.id).order_by(Task.created_at)
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
        UnprocessableRequestError: if a new assignee is not a project member,
            if a new template does not allow the column the card is already
            sitting in, or if a per-column date names a column this card cannot
            be dated in. Retyping a card is not a way to move it, and
            silently moving it would be worse: the card would leave the column
            somebody was looking at it in.
    """
    before = _snapshot(task)
    dated_before = _dated(task)

    # Only the fields in _CLEARABLE can be emptied. A null anywhere else is a
    # client sending back a field it never filled in, not a request to erase a
    # title or unassign the work.
    fields = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in _CLEARABLE
    }

    # Rows of their own rather than a column on the task, so they are written
    # through their own call and taken out of the plain field loop below.
    fields.pop("column_due_dates", None)
    if data.column_due_dates is not None:
        await set_column_due_dates(session, task, data.column_due_dates)

    if "assignee_id" in fields:
        await projects.require_members(session, task.project_id, [fields["assignee_id"]])

    if "goal_id" in fields and fields["goal_id"] != task.goal_id:
        await _refuse_goal_on_subtask(task, fields["goal_id"])
        await goals.require_goal(session, task.project_id, fields["goal_id"])

    if "template_id" in fields and fields["template_id"] != task.template_id:
        new_template = await templates.require_template(
            session, task.project_id, fields["template_id"]
        )
        await _refuse_stranded(session, task, new_template)

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
    await session.refresh(task, ["assignee", "template", "goal"])
    changes = await _diff(session, before, _snapshot(task))
    moved_dates = await _column_dates_changed(session, task, dated_before)
    if moved_dates is not None:
        changes.append(moved_dates)
    return task, changes


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

    # Emptied and flushed before the new rows are added, so replacing a column's
    # date is a delete and an insert rather than an insert onto its own key.
    task.column_due_dates.clear()
    await session.flush()
    task.column_due_dates = [
        TaskColumnDueDate(task_id=task.id, column_id=entry.column_id, due_date=entry.due_date)
        for entry in entries
    ]
    await session.flush()


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
    named = {column.id: column for column in board}
    here = named[task.column_id].position if task.column_id in named else None

    entries = [
        ColumnDue(
            column_id=row.column_id,
            column_name=named[row.column_id].name,
            due_date=row.due_date,
            met=here is not None and here >= named[row.column_id].position,
        )
        for row in task.column_due_dates
        if row.column_id in named
    ]
    entries.sort(key=lambda entry: named[entry.column_id].position)

    finished = (
        task.finished_at is not None
        if task.parent_id is not None
        else bool(board) and task.column_id == board[-1].id
    )
    pending = [entry.due_date for entry in entries if not entry.met]
    if task.due_date is not None and not finished:
        pending.append(task.due_date)
    return BoardDates(entries, min(pending) if pending else None)


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
    # A parent takes its sub-tasks with it (ON DELETE CASCADE), but they are in
    # no column, so the only gap to close is the one the card itself left. A
    # sub-task leaves none at all.
    emptied = task.column_id
    await session.delete(task)
    await session.flush()
    if emptied is not None:
        await _renumber(session, emptied)


async def move(
    session: AsyncSession, task: Task, data: TaskMove
) -> tuple[Task, list[dict[str, Any]]]:
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

    Returns the task and what changed about it, in the same shape
    :func:`update` reports — the column it left and the one it arrived in, and
    the stage it was put back to if that happened. Reordering within one column
    changes nothing worth recording: a card's place in a stack is not a fact
    about the work.

    Raises:
        UnprocessableRequestError: if the task is a sub-task, which is not on
            the board and so has nowhere to be moved to; if the column belongs
            to another project, if the card's template is not allowed in it, if
            the column it is leaving still has the card short of the last
            sub-stage its template set there, or if the destination is the last
            column and a sub-task is still open.
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

    column = await columns.get(session, data.column_id)
    if column.project_id != task.project_id:
        raise UnprocessableRequestError(
            "That column is on a different project's board.",
            details={"column_id": str(column.id)},
        )

    templates.require_permitted(task, column)
    if task.column_id != column.id:
        await _refuse_stage_incomplete(session, task)
    await _refuse_unfinished(session, task, column)

    source_id = _column_of(task)
    was_on = _stage(task.sub_statuses, task.sub_status_index)
    siblings = [
        sibling
        for sibling in await _ordered(session, column.id)
        if sibling.id != task.id  # a move within one column must not count twice
    ]
    siblings.insert(min(data.position, len(siblings)), task)

    task.column_id = column.id
    if source_id != column.id:
        landed_sub_stages = templates.landing_sub_stages(task.template, column.id)
        if landed_sub_stages is not None:
            task.sub_statuses = landed_sub_stages
            task.sub_status_index = 0
        elif task.sub_statuses:
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


async def set_finished(
    session: AsyncSession, task: Task, *, finished: bool
) -> tuple[Task, list[dict[str, Any]]]:
    """Finish a sub-task, or reopen a finished one.

    This is what ticking a sub-task off does, and it is the only way a sub-task
    becomes done: there is no last column for it to be dragged into, because it
    is not on the board. A finished sub-task stops holding its parent back —
    the same effect cancelling it has, said about work that happened rather than
    work that was dropped.

    Finishing something already finished is not an error and does not restate
    the time: the tick box was already ticked, and a second click on it is a
    client catching up rather than a new event to record.

    Returns the task and what changed about it, in the shape :func:`update`
    reports, so a history entry reads the same however the change was made.

    Raises:
        UnprocessableRequestError: if the task is a top-level card, whose done
            is the board's last column and not a field here.
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

    task.finished_at = datetime.now(UTC) if finished else None
    await session.flush()
    return task, [
        {
            "field": "finished",
            "label": "finished",
            "from": "open" if finished else "finished",
            "to": "finished" if finished else "open",
        }
    ]


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
    """Count a project's tasks by status in one query — the hub's headline.

    Sub-tasks are counted. They are not cards and the board does not draw them,
    but a blocked sub-task is genuinely blocked work, and a hub reporting no
    blockages while somebody is waiting on one would be the hub at its least
    useful. How many *cards* there are is :func:`card_count`, which is the
    number that has to agree with the columns.
    """
    rows = await session.execute(
        select(Task.status, func.count()).where(Task.project_id == project.id).group_by(Task.status)
    )
    counts = dict.fromkeys(TaskStatus, 0)
    for status, total in rows:
        counts[status] = total
    return counts


async def card_count(session: AsyncSession, project: Project) -> int:
    """How many cards are on the board — sub-tasks not among them.

    The hub's "tasks" number, and it has to be the columns' counts added up: two
    places saying how big a board is and disagreeing about it is worse than
    either of them being the wrong number.
    """
    total = await session.scalar(
        select(func.count())
        .select_from(Task)
        .where(Task.project_id == project.id, Task.parent_id.is_(None))
    )
    return total or 0


async def subtasks(session: AsyncSession, task: Task) -> list[Task]:
    """The sub-tasks with a reference of their own, in sub-number order.

    Not on the board — they belong to this card and are read here, or on it.
    """
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


async def subtask_counts(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, Split]:
    """How many sub-tasks each card has, and how many are still open.

    Cards and tick boxes counted together, because the rule they answer to is
    one rule: while anything under a card is unsettled it cannot reach the last
    column, and it makes no difference to that whether the outstanding thing has
    a reference of its own.

    Two queries for the whole board rather than a walk per card — these numbers
    appear on every task in every listing. Cancelled items are out of both
    halves rather than counted as done: a card that was split into three and had
    one dropped has two sub-tasks, not three with one of them mysteriously
    complete.
    """
    if not task_ids:
        return {}

    counts: dict[UUID, Split] = {}

    def add(key: UUID, total: int, open_now: int) -> None:
        running = counts.get(key, Split(0, 0))
        counts[key] = Split(running.total + total, running.open + open_now)

    cards = await session.execute(
        select(
            Task.parent_id,
            func.count(),
            func.count().filter(Task.finished_at.is_(None)),
        )
        .where(Task.parent_id.in_(task_ids), Task.status != TaskStatus.CANCELLED)
        .group_by(Task.parent_id)
    )
    for parent_id, total, still_open in cards:
        add(parent_id, total, still_open)

    boxes = await session.execute(
        select(
            TaskChecklistItem.task_id,
            func.count(),
            func.count().filter(TaskChecklistItem.state == ChecklistState.OPEN),
        )
        .where(
            TaskChecklistItem.task_id.in_(task_ids),
            TaskChecklistItem.state != ChecklistState.CANCELLED,
        )
        .group_by(TaskChecklistItem.task_id)
    )
    for task_id, total, still_open in boxes:
        add(task_id, total, still_open)

    return counts


async def subtask_owners(session: AsyncSession, task_ids: list[UUID]) -> dict[UUID, list[Person]]:
    """Who is on each card's sub-tasks, by name, in one query.

    The board no longer shows *where* a sub-task is, so this is what it shows
    instead: the faces on a card are everyone who owes it something, not just
    whoever owns the card itself. Cancelled sub-tasks are left out — nobody is
    on the hook for work that was dropped.
    """
    if not task_ids:
        return {}

    rows = await session.execute(
        select(Task.parent_id, Person)
        .join(Person, Person.id == Task.assignee_id)
        .where(Task.parent_id.in_(task_ids), Task.status != TaskStatus.CANCELLED)
        .order_by(Task.sub_number)
    )

    owners: dict[UUID, list[Person]] = {}
    for parent_id, person in rows:
        seen = owners.setdefault(parent_id, [])
        # In sub-number order and deduplicated, so one person owning three of
        # the sub-tasks is one face on the card rather than three of the same.
        if all(person.id != already.id for already in seen):
            seen.append(person)
    return owners


def _snapshot(task: Task) -> dict[str, Any]:
    """The tracked fields of a task as they stand, for diffing against later."""
    return {field: getattr(task, field) for field in _TRACKED}


def _dated(task: Task) -> dict[UUID, date]:
    """Which columns this card is dated in, and when.

    Copied out of the relationship rather than snapshotted with the rest of the
    fields: the collection is rewritten in place, so a reference to it taken
    beforehand would report the state afterwards.
    """
    return {row.column_id: row.due_date for row in task.column_due_dates}


async def _column_dates_changed(
    session: AsyncSession, task: Task, before: dict[UUID, date]
) -> dict[str, Any] | None:
    """Describe a change to a card's per-column dates, or nothing if none.

    Read in board order and by column name, the way the dates themselves are
    read: "Review 2026-09-20, QA 2026-09-25" says what changed, where a list of
    column ids says only that something did.
    """
    after = _dated(task)
    if after == before:
        return None

    board = await columns.list_for_project(session, task.project)
    return {
        "field": "column_due_dates",
        "label": "column due dates",
        "from": _dates_said(before, board),
        "to": _dates_said(after, board),
    }


def _dates_said(dates: dict[UUID, date], board: list[BoardColumn]) -> str | None:
    said = [
        f"{column.name} {dates[column.id].isoformat()}" for column in board if column.id in dates
    ]
    return ", ".join(said) or None


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
        elif field == "template_id":
            field = "template"
            was, now = await _template_name(session, was), await _template_name(session, now)
        elif field == "goal_id":
            field = "goal"
            was, now = await _goal_name(session, was), await _goal_name(session, now)
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


async def _template_name(session: AsyncSession, template_id: UUID | None) -> str | None:
    if template_id is None:
        return None
    template = await session.get(TaskTemplate, template_id)
    return template.name if template else None


async def _goal_name(session: AsyncSession, goal_id: UUID | None) -> str | None:
    if goal_id is None:
        return None
    goal = await session.get(Goal, goal_id)
    return goal.name if goal else None


async def _refuse_goal_on_subtask(task: Task, goal_id: UUID | None) -> None:
    """Stop a sub-task being put on a goal of its own.

    A sub-task belongs to its card and its card belongs to the goal — see the
    ``goal_only_on_cards`` constraint, which this refuses in the language of
    the request rather than as a database error. Unlinking is always allowed,
    even here: taking a null goal off a sub-task is a no-op, not a rule broken.

    Raises:
        UnprocessableRequestError: if a sub-task is being given a goal.
    """
    if goal_id is None or task.parent_id is None:
        return
    raise UnprocessableRequestError(
        f"{task.reference} is a sub-task, so it cannot be put on a goal of its own. "
        f"Put {task.parent.reference if task.parent else 'its card'} on the goal instead.",
        details={"reference": task.reference},
    )


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


async def _refuse_stranded(
    session: AsyncSession, task: Task, new_template: TaskTemplate | None
) -> None:
    """Stop a card being retyped into a column its new template is barred from.

    Checked against the column the card is *in*, because changing a template is
    not a move: the alternative is a card that quietly relocates when somebody
    edits a dropdown, or one left sitting somewhere its new template forbids.

    Raises:
        UnprocessableRequestError: if the card's current column is not one the
            new template allows.
    """
    # A sub-task sits in no column, so no template can strand it anywhere: the
    # rule a template's stages state is about where a card goes on the board,
    # and this one is not on the board.
    if task.column_id is None:
        return

    permitted = templates.permitted_columns(new_template)
    if not permitted or any(column.id == task.column_id for column in permitted):
        return

    here = await columns.get(session, task.column_id)
    allowed = ", ".join(column.name for column in permitted)
    raise UnprocessableRequestError(
        f"{task.reference} is in {here.name}, which that template does not allow. "
        f"Move it to {allowed} first.",
        details={
            "column": here.name,
            "allowed_columns": [column.name for column in permitted],
            "allowed_column_ids": [str(column.id) for column in permitted],
        },
    )


async def _refuse_stage_incomplete(session: AsyncSession, task: Task) -> None:
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
    here_id = _column_of(task)
    stage = templates.stage_for_column(task.template, here_id)
    if stage is None or not stage.sub_stage_labels:
        return

    last = len(stage.sub_stage_labels) - 1
    index = task.sub_status_index if task.sub_status_index is not None else -1
    if index == last:
        return

    here = await columns.get(session, here_id)
    current = stage.sub_stage_labels[index] if index >= 0 else "not yet started"
    raise UnprocessableRequestError(
        f'{task.reference} is still on "{current}" in {here.name}. Move it to '
        f'"{stage.sub_stage_labels[last]}" before moving this card out of {here.name}.',
        details={
            "column": here.name,
            "current_sub_stage": None if index < 0 else current,
            "target_sub_stage": stage.sub_stage_labels[last],
        },
    )


async def _refuse_unfinished(session: AsyncSession, task: Task, column: BoardColumn) -> None:
    """Stop a card reaching the last column while a sub-task is still open.

    Enforced on the move rather than on the sub-task, because it is a rule about
    the parent: a sub-task may be left open for as long as anybody likes, and
    the moment that matters is the one where its parent claims to be done.

    Raises:
        UnprocessableRequestError: if anything under the task is still open.
    """
    finished = await columns.last(session, task.project_id)
    if column.id != finished.id or task.column_id == finished.id:
        return

    open_cards = [
        subtask.reference
        for subtask in await subtasks(session, task)
        if not subtask.is_settled  # neither finished nor cancelled
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


def _column_of(task: Task) -> UUID:
    """The column a top-level card is in.

    States the invariant ``placed_by_parentage`` enforces rather than checking
    it: everything that asks has already refused a sub-task, which is the only
    kind of task with no column to name.

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
