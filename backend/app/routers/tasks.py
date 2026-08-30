"""Tasks: the cards on a board, and everything that happens to them.

Every path that names a task accepts either its id or its reference, so an
agent can call `/tasks/ATL-41` with the string a human just read out.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import get_session
from app.models.project import Project
from app.models.task import Task, TaskComment
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.people import PersonRead
from app.schemas.tasks import (
    CommentCreate,
    CommentRead,
    TaskCreate,
    TaskDetail,
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
    session: AsyncSession = Depends(get_session),
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


def _read(task: Task, comment_count: int) -> TaskRead:
    return TaskRead(
        id=task.id,
        project_id=task.project_id,
        reference=task.reference,
        number=task.number,
        column_id=task.column_id,
        position=task.position,
        title=task.title,
        description=task.description,
        type=task.type,
        due_date=task.due_date,
        assignee=PersonRead.model_validate(task.assignee),
        status=task.status,
        jira_ref=task.jira_ref,
        pr_ref=task.pr_ref,
        waiting_on=[PersonRead.model_validate(person) for person in task.waiting_on],
        comment_count=comment_count,
        created_at=task.created_at,
    )


async def _detail(session: AsyncSession, task: Task) -> TaskDetail:
    timeline = await tasks.comments(session, task)
    return TaskDetail(
        **_read(task, len(timeline)).model_dump(),
        comments=[_comment(entry) for entry in timeline],
    )


@router.get(
    "/projects/{project_ref}/tasks", response_model=list[TaskRead], summary="List a board's tasks"
)
async def list_tasks(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[TaskRead]:
    """Every card on the board, in the order they are stacked.

    Group by `column_id` to draw the board; the ordering already matches the
    columns left to right and the cards top to bottom within each.
    """
    found = await tasks.list_for_project(session, project)
    counts = await tasks.comment_counts(session, [task.id for task in found])
    return [_read(task, counts.get(task.id, 0)) for task in found]


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
    session: AsyncSession = Depends(get_session),
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
    session: AsyncSession = Depends(get_session),
) -> TaskDetail:
    """One card and its whole timeline."""
    return await _detail(session, task)


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
    session: AsyncSession = Depends(get_session),
) -> TaskDetail:
    """Change any subset of a task's details. Omitted fields are left alone.

    Status is not among them: it moves through `POST /tasks/{ref}/status`,
    which is the only path that can insist on a reason.
    """
    updated = await tasks.update(session, task, body)
    await activity.record(
        session,
        principal,
        "task.updated",
        entity_type="task",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={
            "reference": updated.reference,
            "fields": sorted(body.model_dump(exclude_unset=True)),
        },
    )
    return await _detail(session, updated)


@router.delete("/tasks/{task_ref}", response_model=Acknowledged, summary="Delete a task")
async def delete_task(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
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
    responses={422: {"description": "That column is on another project's board."}},
)
async def move_task(
    body: TaskMove,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> TaskDetail:
    """Put a card in a column, at a position counted from the top.

    Any column, in either direction. A position past the end of the column is
    clamped to it, so "drop at the bottom" needs no length lookup first.
    """
    moved = await tasks.move(session, task, body)
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
        },
    )
    return await _detail(session, moved)


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
    session: AsyncSession = Depends(get_session),
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
        payload={"reference": updated.reference, **entry.meta},
    )
    return await _detail(session, updated)


@router.get(
    "/tasks/{task_ref}/comments", response_model=list[CommentRead], summary="Read the timeline"
)
async def list_comments(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
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
    session: AsyncSession = Depends(get_session),
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
