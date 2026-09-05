"""Goals: the epics a board's cards are work towards.

Every path that names a goal accepts either its id or its reference, so an
agent can call `/goals/ATL-G1` with the string a human just read out.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.goal import Goal
from app.models.project import Project
from app.routers.projects import resolved_project
from app.routers.tasks import read_tasks
from app.schemas.common import Acknowledged
from app.schemas.goals import GoalCreate, GoalDetail, GoalProgress, GoalRead, GoalUpdate
from app.schemas.people import PersonRead
from app.services import activity, goals

router = APIRouter(tags=["goals"])

GoalRef = Path(
    description="The goal's id, or its reference such as `ATL-G1` (case-insensitive).",
    examples=["ATL-G1"],
)


async def resolved_goal(
    goal_ref: str = GoalRef,
    session: AsyncSession = SessionDependency,
) -> Goal:
    """Turn the path segment into a goal, 404-ing if nothing matches."""
    return await goals.resolve(session, goal_ref)


def _read(goal: Goal, progress: GoalProgress) -> GoalRead:
    return GoalRead(
        id=goal.id,
        project_id=goal.project_id,
        reference=goal.reference,
        number=goal.number,
        name=goal.name,
        description=goal.description,
        colour=goal.colour,
        status=goal.status,
        target_date=goal.target_date,
        achieved_at=goal.achieved_at,
        owner=PersonRead.model_validate(goal.owner),
        progress=progress,
        created_at=goal.created_at,
    )


async def _one(session: AsyncSession, goal: Goal) -> GoalRead:
    counted = await goals.progress(session, goal.project_id, [goal.id])
    return _read(goal, counted[goal.id])


async def _detail(session: AsyncSession, goal: Goal) -> GoalDetail:
    linked = await goals.tasks_for(session, goal)
    return GoalDetail(
        **(await _one(session, goal)).model_dump(),
        tasks=await read_tasks(session, linked),
    )


@router.get(
    "/projects/{project_ref}/goals",
    response_model=list[GoalRead],
    summary="List a project's goals",
)
async def list_goals(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    open_only: bool = Query(
        default=False,
        description=(
            "Leave out goals that are achieved or dropped — what a picker "
            "wants, which should not offer work that has stopped."
        ),
    ),
) -> list[GoalRead]:
    """Every goal on the project: open ones first, then those that are settled.

    Within each half the dated ones come first in date order, then the undated
    by name — a list you can plan from rather than one in alphabetical order.

    Each carries its own `progress`, counted from the cards linked to it, so a
    goals page draws itself from this one request.
    """
    found = await goals.list_for_project(session, project, include_settled=not open_only)
    counted = await goals.progress(session, project.id, [goal.id for goal in found])
    return [_read(goal, counted[goal.id]) for goal in found]


@router.post(
    "/projects/{project_ref}/goals",
    response_model=GoalDetail,
    status_code=status.HTTP_201_CREATED,
    summary="Add a goal",
    responses={
        409: {"description": "This project already has a goal by that name."},
        422: {"description": "The owner is not a member of this project."},
    },
)
async def create_goal(
    body: GoalCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> GoalDetail:
    """Start a goal, numbered `ATL-G1` and given a colour.

    Omit `colour` and it takes a stable one from the palette — the same eight
    accents projects and people are drawn from, so a board's rails always look
    like they belong to the same interface.

    Cards are linked to it afterwards, through `PATCH /tasks/{ref}` with a
    `goal_id`: a goal is a heading its cards are written under, and it exists
    before they do.
    """
    goal = await goals.create(session, project, body)
    await activity.record(
        session,
        principal,
        "goal.created",
        entity_type="goal",
        entity_id=goal.id,
        project_id=project.id,
        payload={"reference": goal.reference, "name": goal.name},
    )
    return await _detail(session, goal)


@router.get("/goals/{goal_ref}", response_model=GoalDetail, summary="Get a goal")
async def get_goal(
    goal: Goal = Depends(resolved_goal),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> GoalDetail:
    """One goal, its progress, and the cards linked to it in board order."""
    return await _detail(session, goal)


@router.patch(
    "/goals/{goal_ref}",
    response_model=GoalDetail,
    summary="Update a goal",
    responses={
        409: {"description": "Another goal on this project already has that name."},
        422: {
            "description": (
                "The owner is not a project member, or the goal is being "
                "achieved while cards on it are still open."
            )
        },
    },
)
async def update_goal(
    body: GoalUpdate,
    goal: Goal = Depends(resolved_goal),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> GoalDetail:
    """Change any subset of a goal's details. Omitted fields are left alone.

    `status` is among them. Marking a goal `achieved` is refused while a card
    on it is neither in the board's last column nor cancelled, and the error
    names every card that is holding it open — the same rule that keeps a card
    out of the last column while a sub-task of it is unfinished. `dropped`
    carries no such rule: giving up on a goal is precisely what you do while
    its work is unfinished.
    """
    updated = await goals.update(session, goal, body)
    await activity.record(
        session,
        principal,
        "goal.updated",
        entity_type="goal",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={
            "reference": updated.reference,
            "fields": sorted(body.model_dump(exclude_unset=True)),
        },
    )
    return await _detail(session, updated)


@router.delete("/goals/{goal_ref}", response_model=Acknowledged, summary="Delete a goal")
async def delete_goal(
    goal: Goal = Depends(resolved_goal),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a goal. Its cards stay on the board, unlinked.

    Nothing is refused here: a goal is a label saying what its cards are for,
    and a label can be taken off. To keep the goal but stop it appearing as
    live work, `PATCH` it to `dropped` instead — that keeps its cards, its
    colour and its history.
    """
    reference, name = goal.reference, goal.name
    project_id = goal.project_id
    await goals.delete(session, goal)
    await activity.record(
        session,
        principal,
        "goal.deleted",
        entity_type="goal",
        entity_id=None,
        project_id=project_id,
        payload={"reference": reference, "name": name},
    )
    return Acknowledged()
