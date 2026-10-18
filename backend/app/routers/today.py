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

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core import timezones
from app.db import SessionDependency
from app.models.project import Project
from app.models.today import TodoState
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.today import (
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
    picks = await today.picks_for(session, project, today.person_of(principal), on)
    todos = [todo for picked in picks for todo in picked.todos]
    return TodayPlan(
        project_key=project.key,
        day=on,
        timezone=str(zone),
        picks=[today.pick_read(picked) for picked in picks],
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
    """Pick a card up for a day. Picking one that is already on the day hands it back."""
    task = await tasks.resolve(session, body.task)
    picked = await today.pick(session, project, today.person_of(principal), task, body.day)
    await session.refresh(picked, ["todos"])
    return today.pick_read(picked)


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
    todo = await today.own_todo(session, today.person_of(principal), todo_id)
    return today.todo_read(await today.update_todo(session, todo, body))


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
    moved, left = await today.wrap_up(
        session,
        project,
        today.person_of(principal),
        day=body.day,
        to_day=body.to_day,
        move=body.move,
    )
    return WrapUpResult(day=body.day, to_day=body.to_day, moved=moved, not_done=left)
