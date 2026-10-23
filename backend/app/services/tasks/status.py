"""Whether a card is moving, and if not, why not and who it is waiting on."""

from __future__ import annotations

from sqlalchemy import delete as sql_delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableRequestError
from app.models.task import CommentKind, Task, TaskComment, TaskStatus, TaskWaitingOn
from app.services import goals, projects
from app.services.commands.tasks import TaskStatusChange
from app.services.tasks.parentage import refuse_open_under_finished


async def change_status(
    session: AsyncSession, task: Task, data: TaskStatusChange
) -> tuple[Task, TaskComment]:
    """Move a task between active, on hold and blocked.

    Writes the change to the task's timeline and replaces the set of people it
    is waiting on. Going back to active clears those tags: the question "who is
    holding this up" has no answer once nothing is.

    A task cannot go on hold or become blocked without a reason — a red card
    that does not say why is a question, not information.

    Raises:
        UnprocessableRequestError: if a stalling status arrives without a
            reason, if a tagged person is not a project member, or if an
            unfinished task is being uncancelled under a finished parent or on
            an achieved goal.
    """
    reason = (data.reason or "").strip()
    if data.status is not TaskStatus.ACTIVE and not reason:
        raise UnprocessableRequestError(
            f"Say why this task is {data.status.label.lower()}.",
            details={"field": "reason"},
        )

    tagged = [] if data.status is TaskStatus.ACTIVE else data.waiting_on
    await projects.require_members(session, task.project_id, tagged)
    await _refuse_uncancelling_under_settled(session, task, data.status)

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


async def _refuse_uncancelling_under_settled(
    session: AsyncSession, task: Task, status: TaskStatus
) -> None:
    """Stop a cancelled task coming back open beneath something that is done.

    A cancelled task holds nothing back; the same task active, on hold or
    blocked does, unless it was finished as well. So uncancelling is reopening,
    and is held to what reopening is: a sub-task cannot reopen under a finished
    parent, and a card cannot reopen on an achieved goal. Each check locks the
    row it is about — see :mod:`app.services.tasks.parentage` and
    :func:`app.services.goals.lock`.

    Raises:
        UnprocessableRequestError: naming the parent or the goal.
    """
    reopening = (
        task.status is TaskStatus.CANCELLED
        and status is not TaskStatus.CANCELLED
        and task.finished_at is None
    )
    if not reopening:
        return
    if task.parent is not None:
        await refuse_open_under_finished(
            session, task.parent, act=f"bringing {task.reference} back from cancelled"
        )
    else:
        await goals.refuse_open_card_on_achieved(
            session, task.goal_id, card=task.reference, act="bringing it back from cancelled"
        )
