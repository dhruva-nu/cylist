"""A card's timeline: what people said about it, and the status changes Cylist
wrote on it, in one table and one order."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.task import CommentKind, Task, TaskComment
from app.schemas.tasks import CommentCreate
from app.services import projects
from app.services.paging import PageRequest, Window, keyset


async def comment_page(session: AsyncSession, task: Task, page: PageRequest) -> Window[TaskComment]:
    """One page of the task's timeline, newest first — typed comments and
    status changes together.

    Newest first, as a card's history is, because the end of a timeline is
    where it has got to; reading further back is turning pages.
    """
    return await keyset(
        session,
        select(TaskComment).where(TaskComment.task_id == task.id),
        keys=(TaskComment.created_at, TaskComment.id),
        page=page,
        descending=True,
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


async def get_comment(session: AsyncSession, comment_id: UUID) -> TaskComment:
    """One timeline entry by id.

    Raises:
        NotFoundError: if nothing matches.
    """
    entry = await session.get(TaskComment, comment_id)
    if entry is None:
        raise NotFoundError("No comment with that id.")
    return entry


async def delete_comment(session: AsyncSession, entry: TaskComment) -> None:
    """Remove something somebody said from a card's timeline.

    Only that. A status change sits on the same timeline and in the same table,
    but it is not a remark — it is the card's own record of when it stopped,
    why, and who it was waiting on, written by Cylist rather than typed by
    anybody. A board where that could be taken off the card would be a board
    whose history is an account of what is still convenient, so it is refused
    here rather than left to the caller's judgement.

    Raises:
        UnprocessableRequestError: if the entry is a status change.
    """
    if entry.kind != CommentKind.COMMENT:
        raise UnprocessableRequestError(
            "A status change is part of the card's history and cannot be deleted.",
            details={"kind": entry.kind.value},
        )
    await session.delete(entry)
    await session.flush()


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
