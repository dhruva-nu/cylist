"""Tasks: the cards on a board, and everything that happens to them.

Every path that names a task accepts either its id or its reference, so an
agent can call `/tasks/ATL-41` with the string a human just read out.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.board import BoardColumn
from app.models.person import Person
from app.models.project import Project
from app.models.task import Task, TaskChecklistItem, TaskComment
from app.routers.projects import resolved_project
from app.schemas.agent_sessions import AgentPresence
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.schemas.tasks import (
    ChecklistItemCreate,
    ChecklistItemRead,
    ChecklistItemUpdate,
    ColumnDueDateRead,
    CommentCreate,
    CommentRead,
    SubStatusMove,
    SubtaskCreate,
    TaskCreate,
    TaskDetail,
    TaskFinish,
    TaskHistoryPage,
    TaskMove,
    TaskRead,
    TaskStatusChange,
    TaskUpdate,
)
from app.services import activity, agent_sessions, columns, tasks

router = APIRouter(tags=["tasks"])

TaskRef = Path(
    description="The task's id, or its reference such as `ATL-41` (case-insensitive).",
    examples=["ATL-41"],
)


async def resolved_task(
    task_ref: str = TaskRef,
    session: AsyncSession = SessionDependency,
) -> Task:
    """Turn the path segment into a task, 404-ing if nothing matches."""
    return await tasks.resolve(session, task_ref)


def _comment(entry: TaskComment) -> CommentRead:
    return CommentRead(
        id=entry.id,
        task_id=entry.task_id,
        author=PersonRead.model_validate(entry.author) if entry.author else None,
        body=entry.body,
        kind=entry.kind,
        meta=entry.meta,
        created_at=entry.created_at,
    )


def _item(item: TaskChecklistItem) -> ChecklistItemRead:
    return ChecklistItemRead(
        id=item.id,
        task_id=item.task_id,
        title=item.title,
        state=item.state,
        position=item.position,
        created_at=item.created_at,
    )


def _read(
    task: Task,
    comment_count: int,
    split: tasks.Split = tasks.Split(0, 0),
    owners: list[Person] | None = None,
    board: list[BoardColumn] | None = None,
    presence: AgentPresence | None = None,
) -> TaskRead:
    # Without the board a card's dates cannot be told met from unmet, so they
    # are left off rather than guessed at. The one caller that does this is the
    # sub-task listing, and a sub-task passes through no columns anyway.
    dates = tasks.board_dates(task, board or [])
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        reference=task.reference,
        number=task.number,
        parent_id=task.parent_id,
        parent_reference=task.parent.reference if task.parent else None,
        sub_number=task.sub_number,
        column_id=task.column_id,
        position=task.position,
        title=task.title,
        description=task.description,
        type=task.type,
        priority=task.priority,
        sub_statuses=task.sub_statuses,
        sub_status_index=task.sub_status_index,
        due_date=task.due_date,
        column_due_dates=[
            ColumnDueDateRead(
                column_id=entry.column_id,
                column_name=entry.column_name,
                due_date=entry.due_date,
                met=entry.met,
            )
            for entry in dates.per_column
        ],
        next_due_date=dates.next_due,
        assignee=PersonRead.model_validate(task.assignee),
        status=task.status,
        template_id=task.template_id,
        template_name=task.template.name if task.template else None,
        goal_id=task.goal_id,
        goal_reference=task.goal.reference if task.goal else None,
        goal_name=task.goal.name if task.goal else None,
        goal_colour=task.goal.colour if task.goal else None,
        jira_ref=task.jira_ref,
        pr_ref=task.pr_ref,
        waiting_on=[PersonRead.model_validate(person) for person in task.waiting_on],
        comment_count=comment_count,
        checklist=[_item(item) for item in task.checklist],
        outcome=task.outcome,
        outcome_index=task.outcome_index,
        finished_at=task.finished_at,
        open_subtask_count=split.open,
        subtask_count=split.total,
        subtask_assignees=[PersonRead.model_validate(person) for person in owners or []],
        agent_session=presence,
        created_at=task.created_at,
    )


async def read_tasks(session: AsyncSession, found: list[Task]) -> list[TaskRead]:
    """Render a set of cards, with the counts a card cannot answer alone.

    Comment counts, sub-task progress, sub-task owners and the boards the cards
    sit on each take one query for the whole set rather than one per card.
    Public because the goals router draws the same cards on a goal's page: a
    card should read identically wherever it is listed, which it only does if
    one function is drawing it.
    """
    ids = [task.id for task in found]
    counts = await tasks.comment_counts(session, ids)
    split = await tasks.subtask_counts(session, ids)
    owners = await tasks.subtask_owners(session, ids)
    boards = await _boards(session, found)
    # One query for the agents on every card.
    agents = await agent_sessions.for_tasks(session, ids)
    return [
        _read(
            task,
            counts.get(task.id, 0),
            split.get(task.id, tasks.Split(0, 0)),
            owners.get(task.id),
            boards.get(task.project_id),
            agent_sessions.presence(agents.get(task.id, [])),
        )
        for task in found
    ]


async def _boards(session: AsyncSession, found: list[Task]) -> dict[UUID, list[BoardColumn]]:
    """The columns of every board these cards are on, left to right.

    A card's dates only mean anything against the order of its own board — met
    is "the card has got this far" — and a listing is nearly always one board,
    so it is one query for all of them rather than one per card. Nearly, not
    always: a day's report reaches across projects.
    """
    project_ids = {task.project_id for task in found}
    if not project_ids:
        return {}

    rows = await session.scalars(
        select(BoardColumn)
        .where(BoardColumn.project_id.in_(project_ids))
        .order_by(BoardColumn.position)
    )
    boards: dict[UUID, list[BoardColumn]] = {}
    for column in rows:
        boards.setdefault(column.project_id, []).append(column)
    return boards


async def _detail(session: AsyncSession, task: Task) -> TaskDetail:
    timeline = await tasks.comments(session, task)
    children = await tasks.subtasks(session, task)
    ids = [task.id, *(child.id for child in children)]
    counts = await tasks.subtask_counts(session, ids)
    owners = await tasks.subtask_owners(session, ids)
    board = await columns.list_for_project(session, task.project)
    agents = await agent_sessions.list_for_task(session, task)
    return TaskDetail(
        **_read(
            task,
            len(timeline),
            counts.get(task.id, tasks.Split(0, 0)),
            owners.get(task.id),
            board,
            agent_sessions.presence(agents),
        ).model_dump(),
        comments=[_comment(entry) for entry in timeline],
        agent_sessions=[agent_sessions.read(row) for row in agents],
        # A sub-task cannot be split again, so its own counts are always zero —
        # asked for all the same, because the loop that reads them does not know
        # which of these is which and a special case here would be a lie waiting
        # to come true.
        subtasks=[
            _read(child, 0, counts.get(child.id, tasks.Split(0, 0)), owners.get(child.id))
            for child in children
        ],
    )


@router.get(
    "/projects/{project_ref}/tasks", response_model=list[TaskRead], summary="List a board's tasks"
)
async def list_tasks(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[TaskRead]:
    """Every card on the board, in the order they are stacked.

    Group by `column_id` to draw the board; the ordering already matches the
    columns left to right and the cards top to bottom within each.

    Top-level cards only. A sub-task is not on the board — it comes back with
    the card it belongs to, from `GET /tasks/{ref}`. What a card says about its
    sub-tasks here is `subtask_count`, `open_subtask_count` and
    `subtask_assignees`: how many, how many are left, and who is on them.
    """
    return await read_tasks(session, await tasks.list_for_project(session, project))


@router.post(
    "/projects/{project_ref}/tasks",
    response_model=TaskDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add a task",
    responses={422: {"description": "The assignee is not a member of this project."}},
)
async def create_task(
    body: TaskCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Create a task at the bottom of the board's **first** column.

    There is no column to choose: work enters a board at one end. Move the card
    with `POST /tasks/{ref}/move` immediately afterwards if it belongs
    elsewhere — moving is unrestricted, entering is not.

    Unless, that is, `template_id` names a template whose stages keep it out
    of the first column: then the card lands in the leftmost column that
    template does allow, moving it is restricted to the columns its stages
    name, and it starts on the sub-stages that landing column's stage sets —
    its own `sub_statuses`.
    """
    task = await tasks.create(session, project, body)
    await activity.record(
        session,
        principal,
        "task.created",
        entity_type="task",
        entity_id=task.id,
        project_id=project.id,
        payload={"reference": task.reference, "title": task.title},
    )
    return await _detail(session, task)


@router.get("/tasks/{task_ref}", response_model=TaskDetail, summary="Get a task")
async def get_task(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """One card and its whole timeline."""
    return await _detail(session, task)


@router.get(
    "/tasks/{task_ref}/history",
    response_model=TaskHistoryPage,
    summary="Read a task's history",
)
async def task_history(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: int = Query(default=1, ge=1, description="Which page, counting from 1."),
    per_page: int = Query(default=10, ge=1, le=100, description="Entries per page."),
) -> TaskHistoryPage:
    """What has been done to this card, newest first, a page at a time.

    Distinct from `/comments`, which is what people *said* about the card. This
    is what was *done* to it: every field edit with its old and new value,
    every move, every sub-status step, every checklist item ticked — each with
    the moment it happened and who did it. Because agents act through this same
    API, `channel` says whether a person or a bot was responsible.

    A page past the end is not an error, it is empty: a client holding page 4
    of a history that has since been trimmed should get an empty page and the
    real `pages` count back, not a 404 it has to special-case.

    Only what changed the card is here. Dragging a card up its own column, or
    saving a form without touching a field, is a request the server handled
    rather than something that happened to the work — those stay in
    `/activity`, which is the record of who touched what.

    A sub-task keeps its own history rather than appearing in its parent's: it
    is a card, and its parent's history is about the parent.
    """
    entries, total = await activity.for_entity(
        session, "task", task.id, limit=per_page, offset=(page - 1) * per_page
    )
    # Only the oldest entries need this — a move has recorded both column names
    # itself since CYLIST-8 — but a board is a handful of rows, and a history
    # that says "Moved." and nothing else is the record failing at its one job.
    board = await columns.list_for_project(session, task.project)
    named = {str(column.id): column.name for column in board}
    return TaskHistoryPage(
        entries=[activity.entry_of(entry, named) for entry in entries],
        total=total,
        page=page,
        # At least one page, so "page 1 of 1" reads correctly on an empty
        # history rather than "page 1 of 0".
        pages=max(1, -(-total // per_page)),
        per_page=per_page,
    )


@router.patch(
    "/tasks/{task_ref}",
    response_model=TaskDetail,
    summary="Update a task",
    responses={422: {"description": "The assignee is not a member of this project."}},
)
async def update_task(
    body: TaskUpdate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Change any subset of a task's details. Omitted fields are left alone.

    Status is not among them: it moves through `POST /tasks/{ref}/status`,
    which is the only path that can insist on a reason.
    """
    updated, changes = await tasks.update(session, task, body)
    await activity.record(
        session,
        principal,
        "task.updated",
        entity_type="task",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"reference": updated.reference, "changes": changes},
    )
    return await _detail(session, updated)


@router.delete("/tasks/{task_ref}", response_model=Acknowledged, summary="Delete a task")
async def delete_task(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a task and its comments.

    Its number is retired rather than recycled: `ATL-41` outlives the card in
    commit messages and Jira, so it never names anything else.
    """
    project_id, reference = task.project_id, task.reference
    await tasks.delete(session, task)
    await activity.record(
        session,
        principal,
        "task.deleted",
        entity_type="task",
        project_id=project_id,
        payload={"reference": reference},
    )
    return Acknowledged()


@router.post(
    "/tasks/{task_ref}/move",
    response_model=TaskDetail,
    summary="Move a task",
    responses={
        422: {
            "description": (
                "The task is a sub-task and is not on the board, that column is "
                "on another project's board, or the destination is the last "
                "column and a sub-task is still open."
            )
        }
    },
)
async def move_task(
    body: TaskMove,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Put a card in a column, at a position counted from the top.

    Any column, in either direction. A position past the end of the column is
    clamped to it, so "drop at the bottom" needs no length lookup first.

    Two restrictions. A card with an unfinished sub-task — one not yet ticked
    off, or a checklist item still open — cannot be moved into the last column;
    that is a 422 naming what is still outstanding. And a sub-task cannot be
    moved at all: it is not on the board, and `POST /tasks/{ref}/finish` is what
    finishes one.
    """
    moved, changes = await tasks.move(session, task, body)
    await activity.record(
        session,
        principal,
        "task.moved",
        entity_type="task",
        entity_id=moved.id,
        project_id=moved.project_id,
        payload={
            "reference": moved.reference,
            "column_id": str(moved.column_id),
            "position": moved.position,
            "changes": changes,
        },
    )
    return await _detail(session, moved)


@router.post(
    "/tasks/{task_ref}/sub-status",
    response_model=TaskDetail,
    summary="Move a task's sub-status",
    responses={
        422: {"description": "The task has no sub-statuses set, or there is no such stage."}
    },
)
async def set_sub_status(
    body: SubStatusMove,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Move a task to one of its sub-status stages, forwards or back.

    This is what the board card's own sub-status slider calls: one click puts
    a card on the stage under the cursor without opening it.
    """
    updated, changes = await tasks.set_sub_status(session, task, body.index)
    await activity.record(
        session,
        principal,
        "task.sub_status_moved",
        entity_type="task",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={
            "reference": updated.reference,
            "sub_status_index": updated.sub_status_index,
            "sub_status": updated.sub_statuses[body.index],
            "changes": changes,
        },
    )
    return await _detail(session, updated)


@router.post(
    "/tasks/{task_ref}/finish",
    response_model=TaskDetail,
    summary="Finish a sub-task, or reopen one",
    responses={
        422: {"description": "The task is a card on the board, not a sub-task."},
    },
)
async def finish_task(
    body: TaskFinish,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Tick a sub-task off, or put it back.

    This is how a sub-task is finished, and the only way: it is not on the
    board, so there is no last column to drag it into. A finished sub-task stops
    holding its parent back — the same effect cancelling it has, said about work
    that happened rather than work that was dropped.

    Send `{"finished": false}` to reopen one. Finishing something already
    finished is not an error and does not restate the time.

    On a top-level card this is a 422: a card's done is the board's answer, and
    it is given by moving the card to the last column.
    """
    updated, changes = await tasks.set_finished(session, task, finished=body.finished)
    # Nothing changed means the tick box was already where the caller wants it,
    # and a history line for that would be a record of a click rather than of
    # the work.
    if changes:
        await activity.record(
            session,
            principal,
            "task.finished",
            entity_type="task",
            entity_id=updated.id,
            project_id=updated.project_id,
            payload={
                "reference": updated.reference,
                "parent": updated.parent.reference if updated.parent else None,
                "finished": body.finished,
                "changes": changes,
            },
        )
    return await _detail(session, updated)


@router.post(
    "/tasks/{task_ref}/status",
    response_model=TaskDetail,
    summary="Change a task's status",
    responses={
        422: {"description": "No reason given, or a tagged person is not a project member."}
    },
)
async def change_status(
    body: TaskStatusChange,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Move a task between `active`, `hold` and `blocked`.

    `hold` and `blocked` need a `reason`; without one this is a 422. The reason
    is written to the task's timeline as a `status_change` comment, and
    `waiting_on` replaces whoever the task was previously waiting on. Going
    back to `active` clears those tags.
    """
    updated, entry = await tasks.change_status(session, task, body)
    await activity.record(
        session,
        principal,
        "task.status_changed",
        entity_type="task",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={
            "reference": updated.reference,
            **entry.meta,
            "changes": [
                {
                    "field": "status",
                    "label": "status",
                    "from": entry.meta["from"],
                    "to": entry.meta["to"],
                }
            ],
        },
    )
    return await _detail(session, updated)


@router.get(
    "/tasks/{task_ref}/subtasks",
    response_model=list[TaskRead],
    summary="List a task's sub-tasks",
)
async def list_subtasks(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[TaskRead]:
    """The sub-tasks split out of this one, in sub-number order.

    Only the sub-tasks with a reference of their own. The tick-box kind rides
    along on the task itself, as `checklist`.
    """
    children = await tasks.subtasks(session, task)
    return [_read(child, 0) for child in children]


@router.post(
    "/tasks/{task_ref}/subtasks",
    response_model=TaskDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Split a task into a sub-task with a reference of its own",
    responses={
        422: {
            "description": (
                "The assignee is not a member of this project, or the task is itself a sub-task."
            )
        }
    },
)
async def create_subtask(
    body: SubtaskCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Add a sub-task with a reference, an owner and a timeline of its own.

    It is a task in every respect but placement: an owner, a description, a due
    date, comments, a history, and a reference numbered under its parent —
    `ATL-41-2`. What it does not have is a column. A sub-task is work on a card
    rather than a card on the board, so it is not in `GET
    /projects/{ref}/tasks`, it cannot be moved, and it is finished with `POST
    /tasks/{ref}/finish`.

    Sub-tasks go one level deep; splitting a sub-task again is a 422.

    Until every sub-task is finished or cancelled, the parent cannot be moved to
    the board's last column.
    """
    subtask = await tasks.create(session, task.project, body, parent=task)
    await activity.record(
        session,
        principal,
        "task.subtask_created",
        entity_type="task",
        entity_id=subtask.id,
        project_id=subtask.project_id,
        payload={
            "reference": subtask.reference,
            "parent": task.reference,
            "title": subtask.title,
        },
    )
    return await _detail(session, subtask)


@router.post(
    "/tasks/{task_ref}/checklist",
    response_model=ChecklistItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a checklist item",
)
async def add_checklist_item(
    body: ChecklistItemCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> ChecklistItemRead:
    """Add a tick-box sub-task to the bottom of a task's checklist.

    A line of text with no card, no owner and no reference — the lightweight
    half of sub-tasks. It still has to be ticked or cancelled before the card
    can reach the board's last column.
    """
    item = await tasks.add_checklist_item(session, task, body)
    await activity.record(
        session,
        principal,
        "task.checklist_added",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={"reference": task.reference, "title": item.title},
    )
    return _item(item)


@router.patch(
    "/checklist/{item_id}",
    response_model=ChecklistItemRead,
    summary="Tick, cancel, reopen or retitle a checklist item",
)
async def update_checklist_item(
    body: ChecklistItemUpdate,
    item_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> ChecklistItemRead:
    """Change one tick box.

    `state` is `open`, `done` or `cancelled`; the last two both count as
    settled, so either one stops the item holding its card back.
    """
    item = await tasks.get_checklist_item(session, item_id)
    updated = await tasks.update_checklist_item(session, item, body)
    task = await tasks.resolve(session, str(updated.task_id))
    await activity.record(
        session,
        principal,
        "task.checklist_updated",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={
            "reference": task.reference,
            "title": updated.title,
            "state": updated.state.value,
        },
    )
    return _item(updated)


@router.delete(
    "/checklist/{item_id}", response_model=Acknowledged, summary="Delete a checklist item"
)
async def delete_checklist_item(
    item_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Remove a tick box entirely. Cancel it instead to keep the record."""
    item = await tasks.get_checklist_item(session, item_id)
    task = await tasks.resolve(session, str(item.task_id))
    title = item.title
    await tasks.delete_checklist_item(session, item)
    await activity.record(
        session,
        principal,
        "task.checklist_deleted",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={"reference": task.reference, "title": title},
    )
    return Acknowledged()


@router.get(
    "/tasks/{task_ref}/comments", response_model=list[CommentRead], summary="Read the timeline"
)
async def list_comments(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[CommentRead]:
    """Comments and status changes together, oldest first."""
    return [_comment(entry) for entry in await tasks.comments(session, task)]


@router.post(
    "/tasks/{task_ref}/comments",
    response_model=CommentRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a comment",
    responses={422: {"description": "The author is not a member of this project."}},
)
async def add_comment(
    body: CommentCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> CommentRead:
    """Add a comment to a task.

    `author_id` names the person speaking and must be on the project. An agent
    that is not speaking for anyone in particular omits it.
    """
    entry = await tasks.comment(session, task, body)
    await activity.record(
        session,
        principal,
        "task.commented",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={
            "reference": task.reference,
            "comment_id": str(entry.id),
            # The words, not just the fact of them: a history line reading
            # "Added a comment." sends you to the card to find out what for.
            # Kept in the entry rather than looked up through `comment_id`, so
            # the record still says what was said after the comment is edited.
            "comment": activity.excerpt(entry.body),
        },
    )
    return _comment(entry)
