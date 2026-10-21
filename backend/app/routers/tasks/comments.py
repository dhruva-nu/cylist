"""What people say on a card, read alongside the status changes Cylist wrote."""

from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.task import Task, TaskComment
from app.routers.paging import PageQuery
from app.routers.tasks.dependencies import (
    COMMENT_ON_THIS_CARD,
    may_delete_this_comment,
    resolved_comment,
    resolved_task,
)
from app.routers.tasks.recording import record_on_card
from app.routers.tasks.views import comment_read
from app.schemas.common import Acknowledged
from app.schemas.tasks import CommentCreate, CommentPage, CommentRead
from app.services import activity, mentions, tasks
from app.services.paging import PageRequest

router = APIRouter(tags=["tasks"])


@router.get("/tasks/{task_ref}/comments", response_model=CommentPage, summary="Read the timeline")
async def list_comments(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> CommentPage:
    """Comments and status changes together, newest first, a page at a time.

    Read like `/history`: the newest page first, then further back. `GET
    /tasks/{ref}` already carries the newest entries, and its `comments_next`
    is where this continues from to read the ones before them.
    """
    window = await tasks.comment_page(session, task, page)
    counted = await tasks.comment_counts(session, [task.id])
    return CommentPage(
        items=[comment_read(entry) for entry in window.rows],
        next=window.next,
        total=counted.get(task.id, 0),
    )


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
    principal: Principal = Depends(COMMENT_ON_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> CommentRead:
    """Add a comment to a task.

    `author_id` names the person speaking and must be on the project. An agent
    that is not speaking for anyone in particular omits it.
    """
    entry = await tasks.comment(session, task, body)
    tagged = await mentions.tagged_on_project(session, task.project_id, entry.body)
    await record_on_card(
        session,
        principal,
        "task.commented",
        task,
        {
            "comment_id": str(entry.id),
            # The words, not just the fact of them: a history line reading
            # "Added a comment." sends you to the card to find out what for.
            # Kept in the entry rather than looked up through `comment_id`, so
            # the record still says what was said after the comment is edited.
            "comment": activity.excerpt(entry.body),
            # Who it tagged, read from the whole text rather than the excerpt
            # and kept for the same reason: a hook asking "did this comment
            # tag somebody?" gets the answer as it was when it was said.
            "mentioned": [{"id": str(person.id), "name": person.name} for person in tagged],
        },
    )
    return comment_read(entry)


@router.delete("/comments/{comment_id}", response_model=Acknowledged, summary="Delete a comment")
async def delete_comment(
    entry: TaskComment = Depends(resolved_comment),
    principal: Principal = Depends(may_delete_this_comment),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a comment off a card's timeline.

    Its author may take back what they said; an admin of the board may take
    off anybody's. A status change on the same timeline is the card's history
    rather than a remark, and is refused with a 422.

    The words survive in the card's history, which is the point of having one:
    the line saying the comment was added quoted it when it was written, and
    this writes a second line saying it was taken off.
    """
    task = await tasks.get(session, entry.task_id)
    comment_id, said = entry.id, entry.body
    await tasks.delete_comment(session, entry)
    await record_on_card(
        session,
        principal,
        "task.comment_deleted",
        task,
        {"comment_id": str(comment_id), "comment": activity.excerpt(said)},
    )
    return Acknowledged()
