"""Editing a card where it stands: its fields, and the sub-stage it is on.

Status and column are not here. Each moves through a call of its own — see
:mod:`app.services.tasks.status` and :mod:`app.services.tasks.moves` — because
each has a rule a plain field edit could otherwise walk around.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableRequestError
from app.models.task import Task, TaskStatus
from app.models.template import TaskTemplate
from app.schemas.tasks import TaskUpdate
from app.services import columns, goals, projects, templates
from app.services.tasks.dates import (
    column_dates_change,
    column_due_dates_of,
    set_column_due_dates,
)
from app.services.tasks.history import (
    FieldChange,
    describe_changes,
    stage_change,
    stage_label,
    tracked_values,
)

_CLEARABLE = frozenset({"jira_ref", "due_date", "template_id", "goal_id"})
"""The only task fields a ``PATCH`` may set back to null.

Everything else reads a null as a client echoing back a field it never filled
in. These four are the fields a card can genuinely be without, so for them a
null is the request it looks like: take the date off, drop the link, take the
card out of its template, take it off its goal.

``pr_refs`` is not among them although a card can be without pull requests
too: the empty list already says that, so a null there is the echo it looks
like rather than a second way to spell "none"."""


async def update(
    session: AsyncSession, task: Task, data: TaskUpdate
) -> tuple[Task, list[FieldChange]]:
    """Apply a partial update. Status and column move through their own calls.

    Returns the task and what actually changed about it — field by field, old
    value beside new — which is what the card's history is written from. The
    diff is taken from the row rather than from the request because a ``PATCH``
    routinely sends back fields it is not changing, and a history that says
    "changed the title" every time somebody edits a due date is one nobody
    reads. It is taken *after* the write for the same reason: setting the
    stages moves the current-stage marker, and that move is a change to report
    even though no client asked for it.

    Raises:
        UnprocessableRequestError: if a new assignee is not a project member,
            if a new template does not allow the column the card is already
            sitting in, or if a per-column date names a column this card cannot
            be dated in. Retyping a card is not a way to move it, and
            silently moving it would be worse: the card would leave the column
            somebody was looking at it in.
    """
    before = tracked_values(task)
    column_dates_before = column_due_dates_of(task)
    fields = _fields_to_write(data)

    # Rows of their own rather than a column on the task, so they are written
    # through their own call and taken out of the plain field loop below.
    fields.pop("column_due_dates", None)
    if data.column_due_dates is not None:
        await set_column_due_dates(session, task, data.column_due_dates)

    # The list arrives as `PrRef` models, and the column holds plain JSON: a
    # `model_dump()` leaves the state as an enum member, which is not a thing
    # JSONB can store. Read back off `data` rather than converted in place, so
    # the one shape the column is written in has one owner — `PrRef.stored`.
    if "pr_refs" in fields:
        fields["pr_refs"] = [ref.stored() for ref in data.pr_refs or []]

    await _refuse_invalid_references(session, task, fields)

    if "sub_statuses" in fields or "sub_status_index" in fields:
        _set_sub_statuses(
            task,
            fields.pop("sub_statuses", task.sub_statuses),
            fields.pop("sub_status_index", None),
        )

    for field, value in fields.items():
        setattr(task, field, value)

    await session.flush()
    await session.refresh(task, ["assignee", "template", "goal"])
    changes = await describe_changes(session, before, tracked_values(task))
    dates_change = await column_dates_change(session, task, column_dates_before)
    if dates_change is not None:
        changes.append(dates_change)
    return task, changes


async def set_sub_status(
    session: AsyncSession, task: Task, index: int
) -> tuple[Task, list[FieldChange]]:
    """Move a task to one of its sub-status stages.

    Any stage, in either direction: the board draws this as a slider, and a
    slider that only went forwards would have no way to undo a mis-click. The
    index is taken as given rather than clamped — the caller is pointing at a
    stage it can see, so one out of range means it is looking at a stale list
    and should be told so.

    Raises:
        UnprocessableRequestError: if the task has no sub-statuses set, or the
            index does not name one of them.
    """
    if not task.sub_statuses:
        raise UnprocessableRequestError(
            f"{task.reference} has no sub-statuses set. Add some before moving between them.",
        )
    if not 0 <= index < len(task.sub_statuses):
        raise UnprocessableRequestError(
            f"{task.reference} has {len(task.sub_statuses)} sub-statuses; "
            f"there is no stage {index}.",
        )
    stage_before = stage_label(task.sub_statuses, task.sub_status_index)
    task.sub_status_index = index
    await session.flush()

    stage_after = stage_label(task.sub_statuses, index)
    if stage_after == stage_before:
        return task, []
    return task, [stage_change(stage_before, stage_after)]


def _fields_to_write(data: TaskUpdate) -> dict[str, Any]:
    """The fields a ``PATCH`` asks to set, with its echoed-back nulls dropped.

    Only the fields in :data:`_CLEARABLE` can be emptied. A null anywhere else
    is a client sending back a field it never filled in, not a request to erase
    a title or unassign the work.
    """
    return {
        field: value
        for field, value in data.model_dump(exclude_unset=True).items()
        if value is not None or field in _CLEARABLE
    }


async def _refuse_invalid_references(
    session: AsyncSession, task: Task, fields: dict[str, Any]
) -> None:
    """Check every person, goal and template an update points the card at.

    A goal or template is checked only when it is changing: a client echoing
    back the one the card already has is not asking for anything new, and a
    rule tightened since should not stop it editing the title.

    Raises:
        UnprocessableRequestError: if the assignee is not a project member, if
            the goal or template is another project's, if a sub-task is being
            given a goal, if an open card is being put on an achieved goal, or
            if the new template strands the card.
    """
    if "assignee_id" in fields:
        await projects.require_members(session, task.project_id, [fields["assignee_id"]])

    if "goal_id" in fields and fields["goal_id"] != task.goal_id:
        _refuse_goal_on_subtask(task, fields["goal_id"])
        await goals.require_goal(session, task.project_id, fields["goal_id"])
        if task.finished_at is None and task.status is not TaskStatus.CANCELLED:
            await goals.refuse_open_card_on_achieved(
                session, fields["goal_id"], card=task.reference, act="putting it on the goal"
            )

    if "template_id" in fields and fields["template_id"] != task.template_id:
        new_template = await templates.require_template(
            session, task.project_id, fields["template_id"]
        )
        await _refuse_stranded(session, task, new_template)


def _set_sub_statuses(task: Task, stages: list[str], wanted_index: int | None) -> None:
    """Give a card a new list of sub-stages, keeping the marker on a sensible one.

    Raises:
        UnprocessableRequestError: if ``wanted_index`` is past the last stage.
    """
    if not stages:
        # Nothing to point at, so an index sent alongside is moot rather than
        # wrong: clearing the stages clears the marker.
        task.sub_status_index = None
    elif wanted_index is not None:
        # A caller that reordered or removed a stage knows where the current
        # one ended up. Out of range is a bug on its side, not something to
        # quietly round off.
        if wanted_index >= len(stages):
            raise UnprocessableRequestError(
                f"sub_status_index {wanted_index} is past the last of {len(stages)} sub-statuses.",
            )
        task.sub_status_index = wanted_index
    else:
        # Preserve how far along the card was rather than restarting it — only
        # pulled back as far as the shorter list requires.
        task.sub_status_index = min(task.sub_status_index or 0, len(stages) - 1)
    task.sub_statuses = stages


def _refuse_goal_on_subtask(task: Task, goal_id: UUID | None) -> None:
    """Stop a sub-task being put on a goal of its own.

    A sub-task belongs to its card and its card belongs to the goal — see the
    ``goal_only_on_cards`` constraint, which this refuses in the language of
    the request rather than as a database error. Unlinking is always allowed,
    even here: taking a null goal off a sub-task is a no-op, not a rule broken.

    Raises:
        UnprocessableRequestError: if a sub-task is being given a goal.
    """
    if goal_id is None or task.parent_id is None:
        return
    raise UnprocessableRequestError(
        f"{task.reference} is a sub-task, so it cannot be put on a goal of its own. "
        f"Put {task.parent.reference if task.parent else 'its card'} on the goal instead.",
        details={"reference": task.reference},
    )


async def _refuse_stranded(
    session: AsyncSession, task: Task, new_template: TaskTemplate | None
) -> None:
    """Stop a card being retyped into a column its new template is barred from.

    Checked against the column the card is *in*, because changing a template is
    not a move: the alternative is a card that quietly relocates when somebody
    edits a dropdown, or one left sitting somewhere its new template forbids.

    Raises:
        UnprocessableRequestError: if the card's current column is not one the
            new template allows.
    """
    # A sub-task sits in no column, so no template can strand it anywhere: the
    # rule a template's stages state is about where a card goes on the board,
    # and this one is not on the board.
    if task.column_id is None:
        return

    permitted = templates.permitted_columns(new_template)
    if not permitted or any(column.id == task.column_id for column in permitted):
        return

    here = await columns.get(session, task.column_id)
    allowed = [column.name for column in permitted]
    raise UnprocessableRequestError(
        f"{task.reference} is in {here.name}, which that template does not allow. "
        f"Move it to {', '.join(allowed)} first.",
        details={
            "column": here.name,
            "allowed_columns": allowed,
            "allowed_column_ids": [str(column.id) for column in permitted],
        },
    )
