"""Today: the caller's plan for a day — cards picked up, and to-dos under them.

Every path here answers for the person signed in and nobody else. A plan is
not a change to the board, so nothing here writes to the audit trail; what a
day's plan came to is read straight into that day's report instead.
"""

from __future__ import annotations

from datetime import date
from uuid import UUID

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.card_deps import may_stage_card, may_write_card
from app.api.deps import resolved_project
from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core import timezones
from app.core.errors import NotFoundError
from app.db import SessionDependency
from app.models.project import Project
from app.models.task import ChecklistState
from app.models.today import Todo, TodoState
from app.schemas.common import Acknowledged
from app.schemas.tasks import ChecklistItemUpdate
from app.schemas.today import (
    FreeTodoCreate,
    PickCreate,
    PickRead,
    TodayPlan,
    TodoCreate,
    TodoRead,
    TodoUpdate,
    WrapUp,
    WrapUpResult,
)
from app.services import reports, tasks, today
from app.services.tasks import acts

router = APIRouter(tags=["today"])


@router.get(
    "/projects/{project_ref}/today",
    response_model=TodayPlan,
    summary="Your plan for a day",
)
async def get_plan(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    day: date | None = Query(
        default=None,
        alias="date",
        description="Which day, as `YYYY-MM-DD`. Defaults to today in `timezone`.",
    ),
    timezone: str | None = Query(
        default=None,
        description="IANA zone that says what today is, e.g. `Asia/Kolkata`. Defaults to UTC.",
    ),
) -> TodayPlan:
    """The cards you picked up for a day on this project, each with your to-dos."""
    zone = timezones.zone_for(timezone)
    on = day or reports.today_in(zone)
    person_id = today.person_of(principal)
    # From today on, the plan follows its cards; a day gone by keeps what it had.
    if on >= reports.today_in(zone):
        await today.sync_day(session, project, person_id, on)
    picks = await today.picks_for(session, project, person_id, on)
    free = await today.free_pick(session, project, person_id, on)
    todos = [todo for picked in picks for todo in picked.todos]
    todos += free.todos if free else []
    return TodayPlan(
        project_key=project.key,
        day=on,
        timezone=str(zone),
        picks=[today.pick_read(picked) for picked in picks],
        free_todos=today.free_todos_read(free),
        open_count=sum(todo.state is TodoState.OPEN for todo in todos),
        done_count=sum(todo.state is TodoState.DONE for todo in todos),
    )


@router.post(
    "/projects/{project_ref}/today/picks",
    response_model=PickRead,
    status_code=status.HTTP_201_CREATED,
    summary="Put a card on your day",
)
async def add_pick(
    body: PickCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> PickRead:
    """Pick a card up for a day. Picking one that is already on the day hands it back.

    A card that is not on this project — on another board, or nowhere — is
    one 422, whichever it is.
    """
    task = await tasks.require_on_project(session, project.id, body.task)
    picked = await today.pick(session, project, today.person_of(principal), task, body.day)
    await session.refresh(picked, ["todos"])
    return today.pick_read(picked)


@router.post(
    "/projects/{project_ref}/today/todos",
    response_model=TodoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a to-do that is about no card",
)
async def add_free_todo(
    body: FreeTodoCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TodoRead:
    """Add a to-do to a day without tying it to a card. Tag people with `@` and files with `>`.

    It is ticked, reworded, deleted and wrapped up like any other to-do.
    """
    picked = await today.free_pick(
        session, project, today.person_of(principal), body.day, create=True
    )
    assert picked is not None
    return today.todo_read(await today.add_todo(session, picked, body.text))


@router.delete(
    "/today/picks/{pick_id}",
    response_model=Acknowledged,
    summary="Take a card off your day",
)
async def remove_pick(
    pick_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Take a card off the day. Its to-dos for that day go with it."""
    picked = await today.own_pick(session, today.person_of(principal), pick_id)
    await today.unpick(session, picked)
    return Acknowledged()


@router.post(
    "/today/picks/{pick_id}/todos",
    response_model=TodoRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a to-do",
)
async def add_todo(
    pick_id: UUID,
    body: TodoCreate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TodoRead:
    """Add a to-do under a card on your day. Tag people with `@` and files with `>`."""
    picked = await today.own_pick(session, today.person_of(principal), pick_id)
    return today.todo_read(await today.add_todo(session, picked, body.text))


@router.patch(
    "/today/todos/{todo_id}",
    response_model=TodoRead,
    summary="Tick, untick or reword a to-do",
)
async def update_todo(
    todo_id: UUID,
    body: TodoUpdate,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> TodoRead:
    """Tick, untick or reword a to-do.

    A to-do that came from the card ticks the card too: a sub-task is
    finished or reopened, a tick box ticked or reopened, and a stage moves the
    card on to the stage after it (or back to it, when unticked). Each is
    checked against the caller's role and written to the card's history
    exactly as doing it on the board would be. Giving up on one (`not_done`)
    is the plan's business alone and leaves the card as it is, and so is
    reopening one that was given up on: only unticking a done one unticks the
    card.
    """
    todo = await today.own_todo(session, today.person_of(principal), todo_id)
    # Before the card is touched: a moved to-do is refused whatever it asks.
    today.refuse_moved(todo)
    if todo.source is not None and body.state is not None:
        # Only a real tick or untick reaches the card. Reopening one the day
        # left not done is the plan changing its mind, not the card's work
        # being undone — which may have been finished since.
        if body.state == "done" and todo.state is not TodoState.DONE:
            await _tick_on_card(session, principal, todo, done=True)
        elif body.state == "open" and todo.state is TodoState.DONE:
            await _tick_on_card(session, principal, todo, done=False)
    return today.todo_read(await today.update_todo(session, todo, body))


async def _tick_on_card(
    session: AsyncSession, principal: Principal, todo: Todo, *, done: bool
) -> None:
    """Do to the card what ticking (or unticking) this to-do means.

    Through the same acts and permission rules the board's own routes use, so
    a to-do ticked here is asked and recorded exactly as the board would.
    """
    if todo.source is None:
        return
    kind, ident = today.parse_source(todo.source)
    picked = await today.own_pick(session, today.person_of(principal), todo.pick_id)
    card = picked.task
    if card is None:
        return  # the free pick: nothing on a card to tick

    if kind == "subtask":
        try:
            sub = await tasks.get(session, UUID(ident))
        except (ValueError, NotFoundError):
            return  # deleted since: the to-do is all that is left of it
        await may_write_card(session, principal, sub)
        await acts.finish(session, principal, sub, finished=done)

    elif kind == "checklist":
        item = await today.checklist_item(session, ident)
        if item is None:
            return
        await may_write_card(session, principal, await tasks.get(session, item.task_id))
        state = ChecklistState.DONE if done else ChecklistState.OPEN
        if item.state is not state:
            await acts.update_checklist_item(
                session, principal, item, ChecklistItemUpdate(state=state)
            )

    elif kind == "stage":
        stage = today.parse_stage(ident)
        # A stage of a column the card has since left is not one of its
        # stages any more; the to-do is the plan's alone now.
        if stage is None or stage[0] != card.column_id or card.sub_status_index is None:
            return
        index = stage[1]
        if index >= len(card.sub_statuses):
            return
        # Done with a stage is being on the next one; the last has no next, so
        # finishing it is the plan's to record and the card stays where it is.
        # Unticking one puts the card back on it, if it had got past it.
        target: int | None
        if done:
            target = index + 1 if index + 1 < len(card.sub_statuses) else None
        else:
            target = index if card.sub_status_index > index else None
        if target is None or target == card.sub_status_index:
            return
        await may_stage_card(session, principal, card)
        await acts.move_stage(session, principal, card, target)


@router.delete(
    "/today/todos/{todo_id}",
    response_model=Acknowledged,
    summary="Delete a to-do",
)
async def delete_todo(
    todo_id: UUID,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    todo = await today.own_todo(session, today.person_of(principal), todo_id)
    await today.delete_todo(session, todo)
    return Acknowledged()


@router.post(
    "/projects/{project_ref}/today/wrap-up",
    response_model=WrapUpResult,
    summary="Wrap up a day",
)
async def wrap_up(
    body: WrapUp,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> WrapUpResult:
    """Close a day: move the chosen open to-dos on to `to_day`, leave the rest as not done.

    A moved to-do stays on its day as `moved`, and a copy starts open on
    `to_day` under the same card — which is put on that day if it was not
    there already. To-dos already done are left alone.
    """
    person_id = today.person_of(principal)
    # The card may have moved on since the plan was last read; close the day
    # on what is true now — but only a day from today on, which is all that
    # follows its cards. A day gone by is closed on the plan it had.
    if body.day >= reports.today_in(timezones.zone_for(body.timezone)):
        await today.sync_day(session, project, person_id, body.day)
    moved, left = await today.wrap_up(
        session,
        project,
        person_id,
        day=body.day,
        to_day=body.to_day,
        move=body.move,
    )
    return WrapUpResult(day=body.day, to_day=body.to_day, moved=moved, not_done=left)
