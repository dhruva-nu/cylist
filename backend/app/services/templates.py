"""Task templates: naming them, writing their stages, and the rules they exist
to enforce.

A template's stages say three things at once: which columns its cards may sit
in (:func:`permitted_columns`, asked at creation and on every move), the
sub-stages a card passes through in each (:func:`landing_sub_stages`, asked
whenever a card lands in a column — at creation and on every move — and
loaded onto the card's own ``sub_statuses``; the leaving-is-refused half of
that rule lives in :func:`app.services.tasks._refuse_stage_incomplete`, since
it only needs the stage already on the task, not the template lookup again),
and — on the board's last column alone — which of its outcomes the cards may
end on (:func:`require_outcome_permitted`).

All three keep the same silence: a template that says nothing about a column,
a stage, or an outcome is allowing it, not banning it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.models.template import TaskTemplate, TemplateStage
from app.schemas.templates import StageInput, TemplateCreate, TemplateUpdate

# --- Templates ---------------------------------------------------------------


async def list_templates(session: AsyncSession, project: Project) -> list[TaskTemplate]:
    """The project's templates, in name order."""
    return list(
        await session.scalars(
            select(TaskTemplate)
            .where(TaskTemplate.project_id == project.id)
            .order_by(TaskTemplate.name)
        )
    )


async def get_template(session: AsyncSession, template_id: UUID) -> TaskTemplate:
    template = await session.get(TaskTemplate, template_id)
    if template is None:
        raise NotFoundError("No template with that id.")
    return template


async def create_template(
    session: AsyncSession, project: Project, data: TemplateCreate
) -> TaskTemplate:
    """Add a template to a project, with however many stages it starts with.

    Raises:
        ConflictError: if the project already has one by that name.
        UnprocessableRequestError: if a stage names a column that is not this
            project's, or names a column twice.
    """
    await _refuse_duplicate_template(session, project.id, data.name)
    template = TaskTemplate(project_id=project.id, name=data.name, description=data.description)
    session.add(template)
    await session.flush()
    await _write_stages(session, template, data.stages)
    await session.refresh(template, ["stages"])
    return template


async def update_template(
    session: AsyncSession, template: TaskTemplate, data: TemplateUpdate
) -> TaskTemplate:
    """Rename a template, reword it, or replace its stages outright."""
    fields = data.model_dump(exclude_unset=True)
    name = fields.get("name")
    if name is not None and name.casefold() != template.name.casefold():
        await _refuse_duplicate_template(session, template.project_id, name)

    if name is not None:
        template.name = name
    if fields.get("description") is not None:
        template.description = fields["description"]

    if data.stages is not None:
        for stage in list(template.stages):
            await session.delete(stage)
        await session.flush()
        await _write_stages(session, template, data.stages)

    await session.flush()
    await session.refresh(template, ["stages"])
    return template


async def delete_template(session: AsyncSession, template: TaskTemplate) -> None:
    """Remove a template, and with it every stage written about it.

    Raises:
        ConflictError: if any card was created from it. A card's template is
            what its column rule and its sub-stages are about, so deleting one
            out from under a live card would quietly free it and drop what it
            still owes.
    """
    held = await session.scalar(
        select(func.count()).select_from(Task).where(Task.template_id == template.id)
    )
    if held:
        raise ConflictError(
            f"{held} {'card was' if held == 1 else 'cards were'} created from "
            f"{template.name!r}. Change their template before deleting it.",
            details={"task_count": held},
        )
    await session.delete(template)
    await session.flush()


async def template_task_counts(session: AsyncSession, project_id: UUID) -> dict[UUID, int]:
    """How many cards each of a project's templates has, in one query."""
    rows = await session.execute(
        select(Task.template_id, func.count())
        .where(Task.project_id == project_id, Task.template_id.is_not(None))
        .group_by(Task.template_id)
    )
    return {template_id: total for template_id, total in rows if template_id is not None}


async def require_template(
    session: AsyncSession, project_id: UUID, template_id: UUID | None
) -> TaskTemplate | None:
    """Check a template is one of this project's, and hand it back.

    Raises:
        UnprocessableRequestError: if it belongs to another project, or to no
            project at all.
    """
    if template_id is None:
        return None
    template = await session.get(TaskTemplate, template_id)
    if template is None or template.project_id != project_id:
        raise UnprocessableRequestError(
            "That template is not on this project.",
            details={"template_id": str(template_id)},
        )
    return template


# --- The two rules a template's stages enforce --------------------------------


def permitted_columns(template: TaskTemplate | None) -> list[BoardColumn]:
    """Which columns a card of this template may sit in, left to right.

    An empty list means "anywhere": either the card has no template, or its
    template has no stages yet. Silence is not a ban — see
    :mod:`app.models.template`.
    """
    if template is None or not template.stages:
        return []
    return sorted((stage.column for stage in template.stages), key=lambda column: column.position)


def landing_column(template: TaskTemplate | None, first: BoardColumn) -> BoardColumn:
    """Where a new card of this template lands.

    The board's first column, unless the card's template is not allowed in it
    — then the leftmost column it *is* allowed in. A card has to be born
    somewhere its own template permits.
    """
    permitted = permitted_columns(template)
    if not permitted or any(column.id == first.id for column in permitted):
        return first
    return permitted[0]


def require_permitted(task: Task, column: BoardColumn) -> None:
    """Refuse to put a card in a column its template's stages rule out.

    Raises:
        UnprocessableRequestError: naming the columns the card may go to
            instead, since the caller is holding a board it can see.
    """
    permitted = permitted_columns(task.template)
    if not permitted or any(allowed.id == column.id for allowed in permitted):
        return

    name = task.template.name if task.template else "This card's template"
    allowed = ", ".join(allowed.name for allowed in permitted)
    raise UnprocessableRequestError(
        f"{name} cards do not go to {column.name}. They go to {allowed}.",
        details={
            "template": name,
            "column": column.name,
            "allowed_columns": [allowed.name for allowed in permitted],
            "allowed_column_ids": [str(allowed.id) for allowed in permitted],
        },
    )


def permitted_outcomes(template: TaskTemplate | None, column: BoardColumn) -> list[str]:
    """Which of a column's outcomes this template's cards may end on.

    The column's own list filtered by the template's, in the column's order —
    which is what the board draws and what an error message reads out, so both
    match the sections left to right rather than the order the template was
    written in. An empty template list means all of them; a template list that
    names nothing the column still has means the same, because a rule about
    sections that were renamed away is a rule about nothing.
    """
    stage = stage_for_column(template, column.id)
    if stage is None or not stage.allowed_outcomes:
        return list(column.outcomes)
    allowed = {label.casefold() for label in stage.allowed_outcomes}
    kept = [label for label in column.outcomes if label.casefold() in allowed]
    return kept or list(column.outcomes)


def require_outcome_permitted(task: Task, column: BoardColumn, outcome: str) -> None:
    """Refuse to land a card on an outcome its template's stage rules out.

    Raises:
        UnprocessableRequestError: naming the outcomes the card may end on
            instead.
    """
    allowed = permitted_outcomes(task.template, column)
    if any(label.casefold() == outcome.casefold() for label in allowed):
        return

    name = task.template.name if task.template else "This card's template"
    raise UnprocessableRequestError(
        f"{name} cards do not end on {outcome} in {column.name}. They end on {', '.join(allowed)}.",
        details={
            "template": name,
            "column": column.name,
            "outcome": outcome,
            "allowed_outcomes": allowed,
        },
    )


def stage_for_column(template: TaskTemplate | None, column_id: UUID) -> TemplateStage | None:
    """This template's stage for one column, if it names one."""
    if template is None:
        return None
    return next((stage for stage in template.stages if stage.column_id == column_id), None)


def landing_sub_stages(template: TaskTemplate | None, column_id: UUID) -> list[str] | None:
    """The sub-stage labels a card should start ``column_id`` on, from its
    template.

    ``None`` when the template says nothing about this column — an unstaged
    column asks nothing of the cards that pass through it, so the caller's own
    choice of ``sub_statuses`` stands rather than being overwritten with an
    empty list.
    """
    stage = stage_for_column(template, column_id)
    if stage is None or not stage.sub_stage_labels:
        return None
    return list(stage.sub_stage_labels)


# --- Internals ---------------------------------------------------------------


async def _board(session: AsyncSession, project_id: UUID) -> list[BoardColumn]:
    return list(
        await session.scalars(
            select(BoardColumn)
            .where(BoardColumn.project_id == project_id)
            .order_by(BoardColumn.position)
        )
    )


async def _write_stages(
    session: AsyncSession, template: TaskTemplate, stages: list[StageInput]
) -> None:
    """Replace a template's stages with the ones given, after checking them all.

    Everything is validated before anything is written, so a set with one bad
    stage leaves the template as it was rather than half-rewritten.
    """
    named = [stage.column_id for stage in stages]
    if len(named) != len(set(named)):
        raise UnprocessableRequestError(
            "Name each column at most once. Two stages about one column are "
            "two answers to what it requires.",
        )

    board = {column.id: column for column in await _board(session, template.project_id)}
    strange = [str(column_id) for column_id in named if column_id not in board]
    if strange:
        raise UnprocessableRequestError(
            "A stage names a column that is not on this project's board.",
            details={"column_ids": strange},
        )

    for stage in stages:
        _refuse_unknown_outcomes(board[stage.column_id], stage.allowed_outcomes)

    for stage in stages:
        session.add(
            TemplateStage(
                template_id=template.id,
                column_id=stage.column_id,
                sub_stage_labels=stage.sub_stage_labels,
                allowed_outcomes=stage.allowed_outcomes,
            )
        )
    await session.flush()


def _refuse_unknown_outcomes(column: BoardColumn, allowed: list[str]) -> None:
    """A stage may only narrow the outcomes its column actually has.

    Refused rather than quietly dropped: a template naming an outcome that is
    not there is either a typo or a rule written against a board that has since
    changed, and both are worth being told about while the author is looking.
    """
    if not allowed:
        return
    known = {label.casefold() for label in column.outcomes}
    unknown = [label for label in allowed if label.casefold() not in known]
    if not unknown:
        return
    raise UnprocessableRequestError(
        f"{column.name} has no outcome called {unknown[0]!r}."
        + (f" It has {', '.join(column.outcomes)}." if column.outcomes else " It has none."),
        details={
            "column": column.name,
            "unknown_outcomes": unknown,
            "outcomes": list(column.outcomes),
        },
    )


async def _refuse_duplicate_template(session: AsyncSession, project_id: UUID, name: str) -> None:
    existing = await session.scalar(
        select(TaskTemplate).where(
            TaskTemplate.project_id == project_id,
            func.lower(TaskTemplate.name) == name.casefold(),
        )
    )
    if existing is not None:
        raise ConflictError(
            f"This project already has a template called {existing.name!r}.",
            details={"template_id": str(existing.id)},
        )
