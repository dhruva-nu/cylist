"""A task coming into being, and going away again."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task, TaskStatus
from app.models.template import TaskTemplate
from app.schemas.tasks import TaskCreate
from app.services import columns, goals, projects, templates
from app.services.tasks.dates import set_column_due_dates
from app.services.tasks.numbering import next_number, next_sub_number
from app.services.tasks.stacking import count_cards_in_column, renumber

_LOADED_ON_CREATE = (
    "project",
    "assignee",
    "template",
    "goal",
    "waiting_on",
    "parent",
    "checklist",
    "column_due_dates",
)
"""Every relationship a response reads off a task, loaded once it exists."""


async def create(
    session: AsyncSession,
    project: Project,
    data: TaskCreate,
    *,
    creator_id: UUID | None = None,
    parent: Task | None = None,
) -> Task:
    """Add a task to the bottom of the board's first column.

    The column is the first one *this card* may land in: the board's first,
    unless the card's template is barred from it, in which case the leftmost
    column that template does allow. A card has to be born somewhere its own
    template permits.

    With ``parent`` the new task is a sub-task of it, numbered ``ATL-41-2``
    rather than taking a project number of its own — and it lands in no column
    at all, because a sub-task is work on a card rather than a card on the
    board. A template's stages are rules about columns, so they have nothing to
    say about a sub-task; it may still name one, because what kind of work it is
    stays true whether or not the board is arranging it.

    Args:
        creator_id: Who is writing the card, and so who it lands on when
            ``data`` does not name an assignee — see :func:`_assignee_for`.
            ``None`` where there is nobody: the bootstrap session, and the
            seed script, both of which name the assignee themselves.

    Raises:
        UnprocessableRequestError: if the assignee is not a project member, if
            no assignee was named and none can be assumed, if the template or
            the goal belongs to another project, if a sub-task is given a goal
            of its own, or if ``parent`` is itself a sub-task.
    """
    assignee_id = await _assignee_for(session, project, data, creator_id)
    template = await templates.require_template(session, project.id, data.template_id)
    await goals.require_goal(session, project.id, data.goal_id)

    number: int | None = None
    sub_number: int | None = None
    column: BoardColumn | None = None
    position: int | None = None
    if parent is None:
        number = await next_number(session, project)
        column = templates.landing_column(template, await columns.first(session, project))
        position = await count_cards_in_column(session, column.id)
    else:
        _refuse_unfit_parent(parent, data.goal_id)
        sub_number = await next_sub_number(session, parent)

    sub_statuses = _landing_sub_statuses(template, column, data.sub_statuses)
    task = Task(
        project_id=project.id,
        number=number,
        parent_id=parent.id if parent else None,
        sub_number=sub_number,
        column_id=column.id if column else None,
        position=position,
        title=data.title,
        description=data.description,
        type=data.type,
        priority=data.priority,
        sub_statuses=sub_statuses,
        sub_status_index=0 if sub_statuses else None,
        due_date=data.due_date,
        assignee_id=assignee_id,
        template_id=data.template_id,
        goal_id=data.goal_id,
        status=TaskStatus.ACTIVE,
        jira_ref=data.jira_ref,
        pr_refs=[ref.stored() for ref in data.pr_refs],
    )
    session.add(task)
    await session.flush()

    # Relationships are only populated by a SELECT, and a just-inserted row has
    # not had one. Load them now so callers can read them without lazy IO.
    await session.refresh(task, _LOADED_ON_CREATE)
    await set_column_due_dates(session, task, data.column_due_dates)
    return task


async def delete(session: AsyncSession, task: Task) -> None:
    """Remove a task, closing the gap it leaves in its column.

    Its number is not returned to the pool: ``project.task_counter`` only ever
    goes up, so ``ATL-41`` never names a second task.
    """
    # A parent takes its sub-tasks with it (ON DELETE CASCADE), but they are in
    # no column, so the only gap to close is the one the card itself left. A
    # sub-task leaves none at all.
    emptied = task.column_id
    await session.delete(task)
    await session.flush()
    if emptied is not None:
        await renumber(session, emptied)


async def _assignee_for(
    session: AsyncSession,
    project: Project,
    data: TaskCreate,
    creator_id: UUID | None,
) -> UUID:
    """Who the new card belongs to: whoever was named, or whoever is writing it.

    A card names somebody or it is not a card — see
    :attr:`app.models.task.Task.assignee_id` — so the question an omitted
    ``assignee_id`` asks is not "can this be nobody's?" but "whose, then?", and
    the honest answer is the person in front of it. Work somebody writes down
    is usually work they mean to do; a card that starts on the wrong person is
    one hand-off away from the right one, where a required field is a question
    asked on every card to get the same answer almost every time.

    Raises:
        UnprocessableRequestError: if the assignee named is not a member of the
            project, or if there is nobody to fall back to.
    """
    if data.assignee_id is not None:
        await projects.require_members(session, project.id, [data.assignee_id])
        return data.assignee_id

    if creator_id is None:
        raise UnprocessableRequestError(
            "Say who this card is for. It would otherwise go to whoever created it, and "
            "this credential is nobody in the directory.",
            details={"project": project.key},
        )

    if not await projects.is_member(session, project.id, creator_id):
        raise UnprocessableRequestError(
            f"Say who this card is for. It would otherwise go to you, and you are not on "
            f"{project.key} — add yourself under People, or name somebody who is.",
            details={"project": project.key, "non_member_person_ids": [str(creator_id)]},
        )

    return creator_id


def _refuse_unfit_parent(parent: Task, goal_id: UUID | None) -> None:
    """Stop a sub-task being written somewhere a sub-task cannot go.

    Raises:
        UnprocessableRequestError: if the new sub-task names a goal of its own,
            or if ``parent`` is itself a sub-task.
    """
    if goal_id is not None:
        raise UnprocessableRequestError(
            f"A sub-task cannot be put on a goal of its own. {parent.reference} is what "
            "belongs to a goal; this is work on it.",
            details={"parent": parent.reference, "goal_id": str(goal_id)},
        )

    if parent.parent_id is not None:
        raise UnprocessableRequestError(
            f"{parent.reference} is already a sub-task, so it cannot have sub-tasks of its own. "
            "A board that nests further is a tree, not a board.",
            details={"parent": parent.reference},
        )


def _landing_sub_statuses(
    template: TaskTemplate | None, column: BoardColumn | None, requested: list[str]
) -> list[str]:
    """The sub-stages a new card starts with.

    The template's own sub-stages for the landing column win over whatever the
    caller sent — that is the template's whole point — but a column the
    template says nothing about leaves the caller's choice alone. A sub-task
    lands in no column, so there is no stage to inherit and the caller's own
    list stands.
    """
    if column is None:
        return requested
    from_template = templates.landing_sub_stages(template, column.id)
    return requested if from_template is None else from_template
