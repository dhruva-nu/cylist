"""Turning a task path into rows, and deciding who may act on them.

Every path that names a task accepts either its id or its reference, so an
agent can call `/tasks/ATL-41` with the string a human just read out.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_principal, require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.errors import ForbiddenError, NotFoundError
from app.db import SessionDependency
from app.models.project import Project
from app.models.task import Task, TaskChecklistItem, TaskComment
from app.routers import guards
from app.routers.projects import resolved_project
from app.schemas.tasks import TaskCreate, TaskMove, TaskUpdate
from app.services import columns, permissions, projects, roles, tasks, templates

TaskRef = Path(
    description="The task's id, or its reference such as `ATL-41` (case-insensitive).",
    examples=["ATL-41"],
)


async def resolved_task(
    task_ref: str = TaskRef,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> Task:
    """Turn the path segment into a task, 404-ing if nothing matches — or if
    it is on a project the caller is not a member of."""
    task = await tasks.resolve(session, task_ref)
    await projects.require_reachable(
        session, task.project_id, principal, NotFoundError(f"No task matching {task_ref!r}.")
    )
    return task


async def resolved_checklist_item(
    item_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> TaskChecklistItem:
    """Turn the path segment into a tick box, 404-ing if nothing matches or
    the card it is on is out of the caller's reach."""
    item = await tasks.get_checklist_item(session, item_id)
    task = await tasks.get(session, item.task_id)
    await projects.require_reachable(
        session, task.project_id, principal, NotFoundError("No checklist item with that id.")
    )
    return item


async def resolved_comment(
    comment_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> TaskComment:
    """Turn the path segment into a timeline entry, 404-ing if nothing matches
    or the card it is on is out of the caller's reach."""
    entry = await tasks.get_comment(session, comment_id)
    task = await tasks.get(session, entry.task_id)
    await projects.require_reachable(
        session, task.project_id, principal, NotFoundError("No comment with that id.")
    )
    return entry


WRITE_CARDS = guards.on_project(Permission.TASKS)
"""Adding a card, on the one path that names the project rather than a card."""


async def may_write_card(session: AsyncSession, principal: Principal, task: Task) -> Principal:
    """Whether this caller may change this card — the rule behind :data:`WRITE_THIS_CARD`.

    A plain function as well as a guard, because a card is not always reached
    through its own path: ticking a to-do on a day's plan finishes a sub-task
    or ticks a box on the card it came from, and has to be asked exactly what
    doing it on the board would be asked.
    """
    return await permissions.enforce_on(session, task.project_id, principal, Permission.TASKS)


async def may_stage_card(session: AsyncSession, principal: Principal, task: Task) -> Principal:
    """Whether this caller may set the card's sub-stage — :func:`may_stage_this_card`'s rule.

    A sub-task has no column, so nothing restricts its stages: the rules are
    about places on a board, and it is not on one.
    """
    await may_write_card(session, principal, task)
    if task.column_id is not None:
        await permissions.enforce_column_staging(session, task.project, principal, task.column_id)
    return principal


async def _write_this_card(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    return await may_write_card(session, principal, task)


WRITE_THIS_CARD = _write_this_card
"""Everything done to a card that already exists: editing it, moving it,
finishing it, changing its status, splitting it, ticking its boxes."""

COMMENT_ON_THIS_CARD = guards.for_entity(Permission.COMMENTS, resolved_task)
"""Saying something on a card, which is deliberately not the same permission.
A client who should never move a card is often exactly the person whose
comment you want. The reason a status change carries is not this — that is
part of making the change, and goes with it."""


async def _write_this_checklist(
    item: TaskChecklistItem = Depends(resolved_checklist_item),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    return await may_write_card(session, principal, await tasks.get(session, item.task_id))


WRITE_THIS_CHECKLIST = _write_this_checklist
"""A tick box is addressed by its own id and knows only its card, so it is
asked what changing that card would be."""


async def may_delete_this_comment(
    entry: TaskComment = Depends(resolved_comment),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit whoever said it, or an admin of the board it was said on.

    One verb over two different acts. Taking back what you said yourself is
    the other half of saying it, and is fenced with it — :attr:`COMMENTS`,
    the permission that put the words there. Removing what somebody *else*
    said is moderation: it is not commenting, and no amount of permission to
    comment makes it so, so it belongs to whoever administers the board. The
    same three callers :func:`app.services.roles.may_administer` admits
    everywhere else are the ones who may.

    An entry with no author belongs to nobody — Cylist wrote it, or an agent
    that did not say whose behalf it was speaking on — so only the second path
    reaches those.
    """
    project = (await tasks.get(session, entry.task_id)).project
    if entry.author_id is not None and entry.author_id == principal.person_id:
        return await permissions.enforce(session, project, principal, Permission.COMMENTS)

    if not await roles.may_administer(session, project, principal):
        raise ForbiddenError(
            f"Only an admin of {project.key} can delete a comment somebody else wrote.",
            details={"project_key": project.key},
        )
    return principal


async def may_write_this_card(
    body: TaskUpdate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit an edit whose *fields* this caller's role allows.

    Two of a card's fields answer to something narrower than "may edit cards".
    Its sub-stages belong to the column it is in, and its goal is the one
    goal-shaped thing that happens on a card. Both are checked only when the
    field is actually sent, so an edit that does not touch them is not asked
    about them.
    """
    sent = body.model_fields_set
    if "sub_statuses" in sent and task.column_id is not None:
        await permissions.enforce_column_staging(session, task.project, principal, task.column_id)
    if "goal_id" in sent:
        await permissions.enforce(session, task.project, principal, Permission.GOAL_ASSIGN)
    return principal


async def may_add_card(
    body: TaskCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_CARDS),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit a new card whose fields this caller's role allows.

    The sub-stage check is against the column the card will actually land in
    — the board's first, or the leftmost its template allows — rather than
    against the first column flatly, because a template can send a card
    somewhere else on the way in and that is where its stages would appear.
    """
    if body.goal_id is not None:
        await permissions.enforce(session, project, principal, Permission.GOAL_ASSIGN)

    if not body.sub_statuses:
        return principal

    # Through the same check the service makes, so a template from another
    # project is refused here as it would be there — rather than looked up by
    # id alone, and its stages used to say where a card on this board lands.
    template = await templates.require_template(session, project.id, body.template_id)
    board = await columns.list_for_project(session, project)
    if board:
        landing = templates.landing_column(template, board[0])
        await permissions.enforce_column_staging(session, project, principal, landing.id)
    return principal


async def may_move_this_card(
    body: TaskMove,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit a caller whose role may put a card into the column they named.

    Two checks, and the order matters: `WRITE_THIS_CARD` above asks whether
    they may move cards at all, and this asks whether they may move one
    *there*. A role kept out of Done can still shuffle the rest of the board,
    which is the whole point of the column rules being separate from the flat
    permission rather than a longer list of it.
    """
    await permissions.enforce_column_entry(session, task.project, principal, body.column_id)
    return principal


async def may_stage_this_card(
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Principal:
    """Admit a caller whose role may set sub-stages in the card's own column."""
    return await may_stage_card(session, principal, task)
