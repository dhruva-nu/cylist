"""What changed about a card, said the way its history says it.

Every call that changes a card reports what it changed in one shape — field by
field, old value beside new — and that report is what the card's history is
written from. The shape lives here, once, so an entry reads the same whether
the card was edited, moved, finished or restaged.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any, TypedDict
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import Goal
from app.models.person import Person
from app.models.task import Task
from app.models.template import TaskTemplate

# Spelled with the functional syntax because ``from`` is a keyword, and the
# key is what every stored history entry already calls the old value.
FieldChange = TypedDict(
    "FieldChange",
    {"field": str, "label": str, "from": Any, "to": Any},
)
"""One line of a card's history: which field, how it is named, before and after."""


TRACKED: dict[str, str] = {
    "title": "title",
    "description": "description",
    "type": "type",
    "priority": "priority",
    "sub_statuses": "sub-statuses",
    "sub_status_index": "sub-status",
    "due_date": "due date",
    "assignee_id": "assignee",
    "template_id": "template",
    "goal_id": "goal",
    "jira_ref": "Jira reference",
    "pr_refs": "pull requests",
}
"""The fields a task's history reports on, and how it names them.

Insertion order is the order changes are listed in, so an entry reads down the
card the way the card itself does rather than in whatever order the client
happened to send the fields.
"""

_REPORTED_AS: dict[str, str] = {
    "assignee_id": "assignee",
    "template_id": "template",
    "goal_id": "goal",
}
"""The id fields whose history line reports a name rather than the id.

Reported under the name of the thing rather than of the column, because what
is reported is a person's or a template's name: calling the field
``assignee_id`` beside a name would be a lie about what the value is.
"""


def field_change(field: str, label: str, before: Any, after: Any) -> FieldChange:
    """One history line, in the shape every change to a card is reported in."""
    return {"field": field, "label": label, "from": before, "to": after}


def finished_change(*, was_finished: bool, now_finished: bool) -> FieldChange:
    """The history line for a card being finished or reopened."""
    return field_change(
        "finished",
        "finished",
        "finished" if was_finished else "open",
        "finished" if now_finished else "open",
    )


def outcome_change(before: str | None, after: str | None) -> FieldChange:
    """One history entry's worth of "how this ended" changing."""
    return field_change("outcome", "outcome", before, after)


def stage_change(before: str | None, after: str | None) -> FieldChange:
    """The history line for a card's current sub-stage moving."""
    return field_change("sub_status_index", "sub-status", before, after)


def stage_label(labels: list[str], index: int | None) -> str | None:
    """Name the stage an index points at, for a card that has stages."""
    if index is None or not 0 <= index < len(labels):
        return None
    return labels[index]


def tracked_values(task: Task) -> dict[str, Any]:
    """The tracked fields of a task as they stand, for diffing against later."""
    return {field: getattr(task, field) for field in TRACKED}


async def describe_changes(
    session: AsyncSession, before: dict[str, Any], after: dict[str, Any]
) -> list[FieldChange]:
    """Describe what moved between two snapshots, in :data:`TRACKED` order.

    Values are rendered the way the card renders them — an assignee by name, a
    sub-status by its label — because the history is read by people, and an id
    tells the reader nothing about who picked the work up.
    """
    changes: list[FieldChange] = []
    for field, label in TRACKED.items():
        if before[field] == after[field]:
            continue
        was = await _readable(session, field, before[field], before)
        now = await _readable(session, field, after[field], after)
        changes.append(
            field_change(_REPORTED_AS.get(field, field), label, _json_ready(was), _json_ready(now))
        )
    return changes


async def _readable(session: AsyncSession, field: str, value: Any, snapshot: dict[str, Any]) -> Any:
    """One tracked value as a reader of the card would want it said."""
    match field:
        case "assignee_id":
            return await _name_of(session, Person, value)
        case "template_id":
            return await _name_of(session, TaskTemplate, value)
        case "goal_id":
            return await _name_of(session, Goal, value)
        case "sub_status_index":
            return stage_label(snapshot["sub_statuses"], value)
        case "pr_refs":
            # Said the way the card says it — "#212 (merged)" — because a
            # history entry holding two JSON objects says which fields a pull
            # request has rather than what changed about one.
            return [f"{ref['url']} ({ref['state']})" for ref in value]
        case _:
            return value


async def _name_of[Named: (Person, TaskTemplate, Goal)](
    session: AsyncSession, model: type[Named], row_id: UUID | None
) -> str | None:
    """The name of the person, template or goal an id points at, if it still exists.

    ``session.get`` rather than a query: the row is nearly always already in
    the session, having just been checked by the update that changed it.
    """
    if row_id is None:
        return None
    row = await session.get(model, row_id)
    return row.name if row else None


def _json_ready(value: Any) -> Any:
    """Make a value fit to store in the audit payload, which is JSONB."""
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, list):
        return [_json_ready(item) for item in value]
    return value
