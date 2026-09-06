"""Goals: naming them, colouring them, and counting what is left on them.

Two rules here are choices rather than mechanics:

* **a goal's progress is never stored.** It is counted from the cards linked
  to it every time it is asked for — see :func:`progress`. A percentage kept
  in a column beside the cards is a number that can disagree with them, and
  the cards are the ones telling the truth;
* **a goal cannot be marked achieved while a card on it is still open** — see
  :func:`_refuse_open_cards`, which names what is outstanding. The same shape
  as the rule that keeps a card out of the last column while a sub-task is
  open, and there for the same reason: a heading that claims to be done over
  live work is how work gets forgotten rather than finished. Dropping a goal
  is not refused, because giving up on a goal is exactly the thing you do
  while its work is unfinished.
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import Select, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.palette import GOAL_PALETTE, colour_for
from app.models.board import BoardColumn
from app.models.goal import Goal, GoalStatus
from app.models.project import Project
from app.models.task import Task, TaskStatus
from app.schemas.goals import GoalCreate, GoalProgress, GoalUpdate
from app.services import columns, projects

_SETTLED_LAST = case((Goal.status == GoalStatus.OPEN, 0), else_=1)
"""Open goals first, whatever the alphabet says.

The stored value is a string, so ordering by the column itself would put
``achieved`` and ``dropped`` above ``open`` — which is the list upside down:
what is still being worked towards is what a goals page is for.
"""


async def list_for_project(
    session: AsyncSession, project: Project, *, include_settled: bool = True
) -> list[Goal]:
    """The project's goals: open ones first, then those that are settled.

    Within each half, the dated ones come first in date order, then the undated
    by name. A goal with a date is a goal with a deadline, and a list that
    buries the nearest deadline under an alphabet is a list nobody plans from.
    """
    statement: Select[tuple[Goal]] = select(Goal).where(Goal.project_id == project.id)
    if not include_settled:
        statement = statement.where(Goal.status == GoalStatus.OPEN)
    return list(
        await session.scalars(
            statement.order_by(
                _SETTLED_LAST,
                Goal.target_date.is_(None),
                Goal.target_date,
                Goal.name,
            )
        )
    )


async def get(session: AsyncSession, goal_id: UUID) -> Goal:
    goal = await session.get(Goal, goal_id)
    if goal is None:
        raise NotFoundError("No goal with that id.")
    return goal


async def resolve(session: AsyncSession, reference: str) -> Goal:
    """Look a goal up by id or by reference.

    Args:
        reference: A UUID, or a goal reference such as ``ATL-G1``
            (case-insensitive on the key and on the ``G``).

    Raises:
        NotFoundError: if nothing matches.
    """
    try:
        statement = select(Goal).where(Goal.id == UUID(reference))
    except ValueError:
        # Upper-cased before splitting so ``atl-g1`` reads the same as
        # ``ATL-G1``: a reference read out loud arrives in whatever case the
        # reader typed it.
        key, _, number = reference.upper().partition("-G")
        if not key or not number.isdigit():
            raise NotFoundError(
                f"No goal matching {reference!r}. A goal reference reads ATL-G1 — "
                "the project's key, then G, then its number."
            ) from None
        statement = (
            select(Goal)
            .join(Project, Project.id == Goal.project_id)
            .where(Project.key == key, Goal.number == int(number))
        )

    goal = await session.scalar(statement)
    if goal is None:
        raise NotFoundError(f"No goal matching {reference!r}.")
    return goal


async def find_by_name(session: AsyncSession, project_id: UUID, name: str) -> Goal | None:
    """The project's goal by that name, matched however it was typed.

    For callers holding a name off a screen rather than a reference — the CLI
    and the MCP server both take one, the same way they take a column's name.
    """
    found: Goal | None = await session.scalar(
        select(Goal).where(
            Goal.project_id == project_id,
            func.lower(Goal.name) == name.strip().casefold(),
        )
    )
    return found


async def require_goal(
    session: AsyncSession, project_id: UUID, goal_id: UUID | None
) -> Goal | None:
    """Resolve a goal id that has to belong to this project, or None.

    Raises:
        UnprocessableRequestError: if the id names no goal, or names one on
            another project — a card can only be work towards a goal its own
            board is keeping.
    """
    if goal_id is None:
        return None
    goal = await session.get(Goal, goal_id)
    if goal is None or goal.project_id != project_id:
        raise UnprocessableRequestError(
            "That goal is not on this project.",
            details={"goal_id": str(goal_id)},
        )
    return goal


async def create(session: AsyncSession, project: Project, data: GoalCreate) -> Goal:
    """Add a goal to a project, numbered ``ATL-G1`` and coloured.

    Raises:
        ConflictError: if the project already has a goal by that name.
        UnprocessableRequestError: if the owner is not a project member.
    """
    await projects.require_members(session, project.id, [data.owner_id])
    await _refuse_duplicate(session, project.id, data.name)

    goal = Goal(
        project_id=project.id,
        number=await _next_number(session, project),
        name=data.name,
        description=data.description,
        # A colour taken from the name rather than left to the caller, for the
        # reason a project's is: every goal should look like it belongs to the
        # same interface, and picking one is a decision nobody wanted to make
        # at the moment they were naming a quarter's work.
        colour=data.colour or colour_for(data.name, GOAL_PALETTE),
        target_date=data.target_date,
        owner_id=data.owner_id,
        status=GoalStatus.OPEN,
    )
    session.add(goal)
    await session.flush()
    await session.refresh(goal, ["project", "owner"])
    return goal


async def update(session: AsyncSession, goal: Goal, data: GoalUpdate) -> Goal:
    """Apply a partial update, ``status`` among the fields it may carry.

    Raises:
        ConflictError: if a new name is already another goal's on this project.
        UnprocessableRequestError: if a new owner is not a project member, or
            if the goal is being achieved while a card on it is still open.
    """
    fields = {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        # ``target_date`` alone can be sent back to null: a goal can genuinely
        # be undated, while a null name or owner is a client echoing back a
        # field it never filled in.
        if value is not None or field == "target_date"
    }

    name = fields.get("name")
    if name is not None and name.casefold() != goal.name.casefold():
        await _refuse_duplicate(session, goal.project_id, name)
    if "owner_id" in fields:
        await projects.require_members(session, goal.project_id, [fields["owner_id"]])

    status = fields.pop("status", None)
    for field, value in fields.items():
        setattr(goal, field, value)
    if status is not None and status != goal.status:
        await _set_status(session, goal, status)

    await session.flush()
    await session.refresh(goal, ["project", "owner"])
    return goal


async def delete(session: AsyncSession, goal: Goal) -> None:
    """Remove a goal. Its cards stay, unlinked.

    ``ON DELETE SET NULL`` does the unlinking: a goal is a label saying what a
    card is for, and taking the label off is not a reason to refuse. Its number
    is not returned to the pool — ``project.goal_counter`` only ever goes up,
    so ``ATL-G1`` never names a second goal.
    """
    await session.delete(goal)
    await session.flush()


async def tasks_for(session: AsyncSession, goal: Goal) -> list[Task]:
    """The cards linked to this goal, in board order.

    Left to right by column and top to bottom within one, so a goal's page
    reads as the slice of the board that belongs to it. Cards only: a sub-task
    cannot name a goal, and arrives with the card it belongs to.
    """
    return list(
        await session.scalars(
            select(Task)
            .join(BoardColumn, BoardColumn.id == Task.column_id)
            .where(Task.goal_id == goal.id)
            .order_by(BoardColumn.position, Task.position)
        )
    )


async def progress(
    session: AsyncSession, project_id: UUID, goal_ids: list[UUID]
) -> dict[UUID, GoalProgress]:
    """How far along each of these goals is, in one query.

    Counted from the cards rather than stored on the goal — see this module's
    docstring. A goal nobody has linked a card to yet comes back all zeros
    rather than missing, so a caller never has to ask whether the absence
    means "none" or "not counted".
    """
    empty = GoalProgress(total=0, done=0, cancelled=0, open=0, blocked=0, on_hold=0)
    counts = {goal_id: empty.model_copy() for goal_id in goal_ids}
    if not goal_ids:
        return counts

    finished = await columns.last(session, project_id)
    rows = await session.execute(
        select(Task.goal_id, Task.column_id, Task.status, func.count())
        .where(Task.goal_id.in_(goal_ids), Task.parent_id.is_(None))
        .group_by(Task.goal_id, Task.column_id, Task.status)
    )

    for goal_id, column_id, status, total in rows.tuples():
        if goal_id is None:
            continue  # unreachable: the WHERE named the goals. Narrowing only.
        tally = counts[goal_id]
        tally.total += total
        # Cancelled first: a card that was dropped in the last column is
        # dropped, not done. Only one of the three can be true of a card, which
        # is what keeps done + cancelled + open equal to total.
        if status is TaskStatus.CANCELLED:
            tally.cancelled += total
        elif column_id == finished.id:
            tally.done += total
        else:
            tally.open += total
            if status is TaskStatus.BLOCKED:
                tally.blocked += total
            elif status is TaskStatus.HOLD:
                tally.on_hold += total
    return counts


async def counts_for_project(session: AsyncSession, project: Project) -> tuple[int, int]:
    """How many goals the project has, and how many are still open.

    The pair the hub's card shows. One query rather than two, because the
    second number is a subset of the first and two round trips to say so is one
    too many.
    """
    rows = await session.execute(
        select(Goal.status, func.count()).where(Goal.project_id == project.id).group_by(Goal.status)
    )
    # ``.all()`` first, and not for tidiness: a Result has a ``keys()`` method,
    # so ``dict(rows)`` takes the result itself for a mapping and asks it for
    # rows by column name. The list of pairs is what dict() wants.
    by_status = dict(rows.tuples().all())
    return sum(by_status.values()), by_status.get(GoalStatus.OPEN, 0)


async def _set_status(session: AsyncSession, goal: Goal, status: GoalStatus) -> None:
    """Move a goal between open, achieved and dropped.

    ``achieved_at`` is written here rather than by the caller so that the
    timestamp and the status can never be set apart from each other.
    """
    if status is GoalStatus.ACHIEVED:
        await _refuse_open_cards(session, goal)
        goal.achieved_at = datetime.now(UTC)
    else:
        # Reopening or dropping clears it: a goal that is not achieved has no
        # moment at which it was.
        goal.achieved_at = None
    goal.status = status


async def _refuse_open_cards(session: AsyncSession, goal: Goal) -> None:
    """Stop a goal being marked achieved over cards that are still open.

    Raises:
        UnprocessableRequestError: naming the cards still outstanding.
    """
    finished = await columns.last(session, goal.project_id)
    outstanding = [
        task
        for task in await tasks_for(session, goal)
        if task.status is not TaskStatus.CANCELLED and task.column_id != finished.id
    ]
    if not outstanding:
        return

    references = [task.reference for task in outstanding]
    raise UnprocessableRequestError(
        f"{goal.reference} still has {len(references)} open "
        f"{'card' if len(references) == 1 else 'cards'}: {', '.join(references)}. "
        f"Finish or cancel each of them — or unlink it from the goal — before "
        f"marking {goal.name} achieved.",
        details={"open_tasks": references, "column": finished.name},
    )


async def _refuse_duplicate(session: AsyncSession, project_id: UUID, name: str) -> None:
    """Raises:
    ConflictError: if the project already has a goal by that name, however it
        was capitalised. A card wears its goal's name, and two alike make the
        card ambiguous to read.
    """
    existing = await find_by_name(session, project_id, name)
    if existing is not None:
        raise ConflictError(
            f"This project already has a goal called {existing.name!r}.",
            details={"name": existing.name, "reference": existing.reference},
        )


async def _next_number(session: AsyncSession, project: Project) -> int:
    """Take the next goal number for a project.

    Locks the project row, for the reason ``tasks._next_number`` does: a
    deleted goal's number must never come back, and two callers creating a goal
    at the same moment must not be handed the same one.
    """
    counter = await session.scalar(
        select(Project.goal_counter).where(Project.id == project.id).with_for_update()
    )
    project.goal_counter = (counter or 0) + 1
    await session.flush()
    return project.goal_counter
