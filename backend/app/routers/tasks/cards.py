"""The cards themselves: adding, reading, editing, moving and finishing them."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.routers.columns import resolved_column
from app.routers.paging import PageQuery
from app.routers.projects import resolved_project
from app.routers.tasks import acts
from app.routers.tasks.dependencies import (
    WRITE_THIS_CARD,
    may_add_card,
    may_move_this_card,
    may_stage_this_card,
    may_write_this_card,
    resolved_task,
)
from app.routers.tasks.recording import record_on_card
from app.routers.tasks.views import read_tasks, task_detail
from app.schemas.common import Acknowledged
from app.schemas.tasks import (
    BoardTasks,
    ColumnTasks,
    SubStatusMove,
    SubtaskCreate,
    TaskCreate,
    TaskDetail,
    TaskFinish,
    TaskHistoryPage,
    TaskMove,
    TaskStatusChange,
    TaskSummaryPage,
    TaskUpdate,
)
from app.services import activity, columns, tasks
from app.services.paging import DEFAULT_LIMIT, MAX_LIMIT, PageRequest

router = APIRouter(tags=["tasks"])


@router.get(
    "/projects/{project_ref}/tasks",
    response_model=BoardTasks,
    summary="List a board's tasks, a page per column",
)
async def list_tasks(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    limit: int = Query(
        default=DEFAULT_LIMIT,
        ge=1,
        le=MAX_LIMIT,
        description=f"How many cards to return from the top of each column, at most {MAX_LIMIT}.",
    ),
) -> BoardTasks:
    """The top of every column, left to right, and how many cards each holds.

    Each column's `items` are its cards top to bottom; where a column holds
    more than `limit`, its `next` is the cursor for the rest, read with
    `GET /columns/{column_id}/tasks?after=…`. A board is read this way rather
    than whole because a column can hold thousands of cards and a screen shows
    a few dozen (CYLIST-130).

    The cards are summaries: everything but `description`, which is read with
    `GET /tasks/{ref}`.

    Top-level cards only. A sub-task is not on the board — it comes back with
    the card it belongs to, from `GET /tasks/{ref}`. What a card says about its
    sub-tasks here is `subtask_count`, `open_subtask_count` and
    `subtask_assignees`: how many, how many are left, and who is on them.
    """
    stacks = await tasks.board_pages(session, project, limit=limit)
    drawn = iter(
        await read_tasks(session, [task for stack in stacks for task in stack.window.rows])
    )
    return BoardTasks(
        columns=[
            ColumnTasks(
                column_id=stack.column.id,
                total=stack.total,
                items=[next(drawn) for _ in stack.window.rows],
                next=stack.window.next,
            )
            for stack in stacks
        ]
    )


@router.get(
    "/columns/{column_id}/tasks",
    response_model=TaskSummaryPage,
    summary="Read a column of the board, a page at a time",
)
async def list_column_tasks(
    column: BoardColumn = Depends(resolved_column),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> TaskSummaryPage:
    """One column's cards, top to bottom, continuing from a column's `next`.

    `GET /projects/{ref}/tasks` returns the top of every column at once; this
    is what reads further down one of them. A cursor whose card has since left
    the column — moved, deleted, stored — is a 422, and the column is read
    again from the top.
    """
    window = await tasks.column_page(session, column, page)
    return TaskSummaryPage(items=await read_tasks(session, window.rows), next=window.next)


@router.post(
    "/projects/{project_ref}/tasks",
    response_model=TaskDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add a task",
    responses={
        422: {
            "description": (
                "The assignee is not a member of this project — or none was named and "
                "the caller is not one either."
            )
        }
    },
)
async def create_task(
    body: TaskCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(may_add_card),
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

    The card lands on **you** unless `assignee_id` names somebody else: a card
    is the writer's until they hand it on, which `PATCH /tasks/{ref}` does.
    """
    task = await tasks.create(session, project, body, creator_id=principal.person_id)
    await record_on_card(session, principal, "task.created", task, {"title": task.title})
    return await task_detail(session, task)


@router.get("/tasks/{task_ref}", response_model=TaskDetail, summary="Get a task")
async def get_task(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """One card and its whole timeline."""
    return await task_detail(session, task)


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
    column_names = {str(column.id): column.name for column in board}
    # At least one page, so "page 1 of 1" reads correctly on an empty history
    # rather than "page 1 of 0".
    pages = max(1, (total + per_page - 1) // per_page)
    return TaskHistoryPage(
        entries=[activity.entry_of(entry, column_names) for entry in entries],
        total=total,
        page=page,
        pages=pages,
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
    principal: Principal = Depends(may_write_this_card),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Change any subset of a task's details. Omitted fields are left alone.

    Status is not among them: it moves through `POST /tasks/{ref}/status`,
    which is the only path that can insist on a reason.
    """
    updated, changes = await tasks.update(session, task, body)
    await record_on_card(session, principal, "task.updated", updated, {"changes": changes})
    return await task_detail(session, updated)


@router.delete("/tasks/{task_ref}", response_model=Acknowledged, summary="Delete a task")
async def delete_task(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a task and its comments.

    Its number is retired rather than recycled: `ATL-41` outlives the card in
    commit messages and Jira, so it never names anything else.
    """
    project_id, reference = task.project_id, task.reference
    await tasks.delete(session, task)
    # No entity id: the card is gone, and the reference is what the entry
    # still has to name it by.
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
    principal: Principal = Depends(may_move_this_card),
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
    # Taken before the move, which changes it in place. The changes below name
    # the column the card left; a hook filtering on it needs its id.
    from_column_id = task.column_id
    moved, changes = await tasks.move(session, task, body)
    await record_on_card(
        session,
        principal,
        "task.moved",
        moved,
        {
            "column_id": str(moved.column_id),
            "from_column_id": str(from_column_id) if from_column_id else None,
            "position": moved.position,
            "changes": changes,
        },
    )
    return await task_detail(session, moved)


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
    principal: Principal = Depends(may_stage_this_card),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Move a task to one of its sub-status stages, forwards or back.

    This is what the board card's own sub-status slider calls: one click puts
    a card on the stage under the cursor without opening it.
    """
    updated = await acts.move_stage(session, principal, task, body.index)
    return await task_detail(session, updated)


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
    principal: Principal = Depends(WRITE_THIS_CARD),
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
    updated = await acts.finish(session, principal, task, finished=body.finished)
    return await task_detail(session, updated)


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
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> TaskDetail:
    """Move a task between `active`, `hold` and `blocked`.

    `hold` and `blocked` need a `reason`; without one this is a 422. The reason
    is written to the task's timeline as a `status_change` comment, and
    `waiting_on` replaces whoever the task was previously waiting on. Going
    back to `active` clears those tags.
    """
    updated, entry = await tasks.change_status(session, task, body)
    status_change = tasks.field_change("status", "status", entry.meta["from"], entry.meta["to"])
    await record_on_card(
        session,
        principal,
        "task.status_changed",
        updated,
        {**entry.meta, "changes": [status_change]},
    )
    return await task_detail(session, updated)


@router.get(
    "/tasks/{task_ref}/subtasks",
    response_model=TaskSummaryPage,
    summary="List a task's sub-tasks",
)
async def list_subtasks(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> TaskSummaryPage:
    """The sub-tasks split out of this one, in sub-number order, a page at a time.

    Only the sub-tasks with a reference of their own. The tick-box kind rides
    along on the task itself, as `checklist`.
    """
    window = await tasks.subtask_page(session, task, page)
    return TaskSummaryPage(items=await read_tasks(session, window.rows), next=window.next)


@router.post(
    "/tasks/{task_ref}/subtasks",
    response_model=TaskDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Split a task into a sub-task with a reference of its own",
    responses={
        422: {
            "description": (
                "The assignee is not a member of this project — or none was named and the "
                "caller is not one either — or the task is itself a sub-task."
            )
        }
    },
)
async def create_subtask(
    body: SubtaskCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
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

    Its owner is **you** unless `assignee_id` names somebody else — splitting a
    card is usually taking a piece of it, not handing one out.
    """
    subtask = await tasks.create(
        session, task.project, body, creator_id=principal.person_id, parent=task
    )
    await record_on_card(
        session,
        principal,
        "task.subtask_created",
        subtask,
        {"parent": task.reference, "title": subtask.title},
    )
    return await task_detail(session, subtask)
