"""Tasks: the cards on a board, and everything that happens to them.

Every path that names a task accepts either its id or its reference, so an
agent can call `/tasks/ATL-41` with the string a human just read out.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.project import Project
from app.models.task import Task, TaskChecklistItem, TaskComment
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.schemas.tasks import (
    ChecklistItemCreate,
    ChecklistItemRead,
    ChecklistItemUpdate,
    CommentCreate,
    CommentRead,
    SubStatusMove,
    SubtaskCreate,
    TaskCreate,
    TaskDetail,
    TaskHistoryPage,
    TaskMove,
    TaskRead,
    TaskStatusChange,
    TaskUpdate,
)
from app.services import activity, tasks

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


def _read(task: Task, comment_count: int, open_subtasks: int = 0) -> TaskRead:
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
        assignee=PersonRead.model_validate(task.assignee),
        status=task.status,
        jira_ref=task.jira_ref,
        pr_ref=task.pr_ref,
        waiting_on=[PersonRead.model_validate(person) for person in task.waiting_on],
        comment_count=comment_count,
        checklist=[_item(item) for item in task.checklist],
        open_subtask_count=open_subtasks,
        created_at=task.created_at,
    )


async def _detail(session: AsyncSession, task: Task) -> TaskDetail:
    timeline = await tasks.comments(session, task)
    children = await tasks.subtasks(session, task)
    counts = await tasks.open_subtask_counts(
        session, task.project_id, [task.id, *(child.id for child in children)]
    )
    return TaskDetail(
        **_read(task, len(timeline), counts.get(task.id, 0)).model_dump(),
        comments=[_comment(entry) for entry in timeline],
        subtasks=[_read(child, 0, counts.get(child.id, 0)) for child in children],
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
    """
    found = await tasks.list_for_project(session, project)
    ids = [task.id for task in found]
    counts = await tasks.comment_counts(session, ids)
    open_subtasks = await tasks.open_subtask_counts(session, project.id, ids)
    return [_read(task, counts.get(task.id, 0), open_subtasks.get(task.id, 0)) for task in found]


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
    return TaskHistoryPage(
        entries=[activity.entry_of(entry) for entry in entries],
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
                "That column is on another project's board, or the destination "
                "is the last column and a sub-task is still open."
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

    One restriction: a card with an unfinished sub-task — a card of its own not
    yet in the last column, or a checklist item still open — cannot be moved
    into the last column. That is a 422 naming what is still outstanding.
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
    """The cards split out of this one, in sub-number order.

    Only the sub-tasks that have a card of their own. The tick-box kind rides
    along on the task itself, as `checklist`.
    """
    children = await tasks.subtasks(session, task)
    counts = await tasks.open_subtask_counts(
        session, task.project_id, [child.id for child in children]
    )
    return [_read(child, 0, counts.get(child.id, 0)) for child in children]


@router.post(
    "/tasks/{task_ref}/subtasks",
    response_model=TaskDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Split a task into a sub-task",
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
    """Add a sub-task that gets its own card on the board.

    It is a task in every respect — first column, owner, due date — except its
    reference, which is numbered under its parent: `ATL-41-2`. Sub-tasks go one
    level deep; splitting a sub-task again is a 422.

    Until every sub-task is in the board's last column or cancelled, the parent
    cannot be moved there.
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
        payload={"reference": task.reference, "comment_id": str(entry.id)},
    )
    return _comment(entry)
