"""What a delivery carries: the envelope every event has, and the card or column inside it."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now
from app.models.activity import Activity, EntityType, Verb
from app.models.board import BoardColumn
from app.models.hook import Hook
from app.models.person import Person
from app.models.project import Project
from app.models.task import Task
from app.models.template import TaskTemplate


@dataclass(frozen=True)
class Card:
    """What a change knew about the card it was made to, looked up once."""

    task: Task | None
    moved_column: bool
    """A move that changed column — not a reorder, not a change of section."""
    from_column_id: UUID | None
    assigned_to_agent: bool
    """Whether the card's owner, after the change, is the agent."""
    mentioned: frozenset[UUID] = frozenset()
    """Who the comment tagged, on a ``task.commented``. Empty on anything else."""


async def event_body(session: AsyncSession, entry: Activity, card: Card) -> dict[str, Any]:
    """The JSON a receiver gets: what happened, to which card, by whom.

    Names as well as ids throughout, because the receiver is usually a script
    deciding something on sight — "is this the In staging column?" — and should
    not need a second call back into Cylist to find out.
    """
    # Imported here: activity calls into this module on every write, so at
    # module scope the two would be a cycle.
    from app.services.activity import describe

    project = await session.get(Project, entry.project_id)
    task = card.task
    body: dict[str, Any] = {
        "id": str(entry.id),
        "event": entry.verb,
        "summary": describe(entry),
        "occurred_at": now().isoformat(),
        "project": (
            {"id": str(project.id), "key": project.key, "name": project.name}
            if project is not None
            else None
        ),
        "actor": {
            "name": entry.actor_label,
            "person_id": str(entry.actor_person_id) if entry.actor_person_id else None,
            "channel": str(entry.channel),
        },
        "task": await _task_body(session, task) if task is not None else None,
        "changes": entry.payload.get("changes") or [],
        "detail": entry.payload,
    }
    if task is None and entry.entity_type == EntityType.TASK:
        # A deleted card is gone by now; its reference is what the trail kept.
        body["task"] = {"reference": entry.payload.get("reference")}
    if card.moved_column and task is not None:
        body["from_column"] = await _column_body(session, card.from_column_id)
        body["to_column"] = await _column_body(session, task.column_id)
    return body


async def _task_body(session: AsyncSession, task: Task) -> dict[str, Any]:
    # Each looked up by id rather than through the relationship: a change made
    # in this request may have moved the id without refreshing what the
    # relationship loaded before it.
    template = await session.get(TaskTemplate, task.template_id) if task.template_id else None
    assignee = await session.get(Person, task.assignee_id)
    return {
        "id": str(task.id),
        "reference": task.reference,
        "title": task.title,
        "type": str(task.type),
        "status": str(task.status),
        "priority": str(task.priority),
        "column": await _column_body(session, task.column_id),
        "template": {"id": str(template.id), "name": template.name} if template else None,
        "assignee": {"id": str(assignee.id), "name": assignee.name} if assignee else None,
        "parent_id": str(task.parent_id) if task.parent_id else None,
        "finished_at": task.finished_at.isoformat() if task.finished_at else None,
    }


async def _column_body(session: AsyncSession, column_id: UUID | None) -> dict[str, Any] | None:
    if column_id is None:
        return None
    column = await session.get(BoardColumn, column_id)
    return {"id": str(column_id), "name": column.name if column else None}


def sample_body(project: Project, hook: Hook, principal: Principal) -> dict[str, Any]:
    """What "Send test" sends: the envelope every event has, about nothing.

    Shaped like a real event so a receiver can be written against it, and
    named ``hook.test`` so it can tell the difference."""
    return _bare_body(
        project, principal, event="hook.test", summary=f"A test from the hook {hook.name!r}."
    )


def fired_body(
    project: Project, hook: Hook, principal: Principal, message: str | None
) -> dict[str, Any]:
    """What firing a manual hook sends: the same envelope, about the project
    rather than a card, with whatever the person firing it said in ``detail``."""
    return _bare_body(
        project,
        principal,
        event=Verb.HOOK_FIRED,
        summary=f"{principal.label} fired the hook {hook.name!r}.",
        detail={"hook": {"id": str(hook.id), "name": hook.name}, "message": message},
    )


def _bare_body(
    project: Project,
    principal: Principal,
    *,
    event: str,
    summary: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "id": None,
        "event": event,
        "summary": summary,
        "occurred_at": now().isoformat(),
        "project": {"id": str(project.id), "key": project.key, "name": project.name},
        "actor": {
            "name": principal.label,
            "person_id": str(principal.person_id) if principal.person_id else None,
            "channel": str(principal.channel),
        },
        "task": None,
        "changes": [],
        "detail": detail or {},
    }
