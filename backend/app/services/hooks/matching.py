"""Which hooks a change fires, and queueing a delivery to each."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now
from app.db import publish_into
from app.models.activity import Activity, EntityType, Verb
from app.models.hook import Hook, HookDelivery
from app.models.person import Person
from app.models.task import Task
from app.realtime.events import HooksQueued
from app.services.activity import Recorded
from app.services.hooks.catalogue import EVENTS
from app.services.hooks.payload import Card, event_body

# --- Matching a change -----------------------------------------------------


async def on_recorded(session: AsyncSession, recorded: Recorded) -> None:
    """The activity trail's subscriber: queue a delivery to every hook the change matches.

    Queued in the transaction that made the change, so one that rolls back is
    never announced and one that commits is never lost; the courier is woken once
    it has committed.
    """
    if await queue_for(session, recorded.entry):
        publish_into(session, HooksQueued())


async def queue_for(session: AsyncSession, entry: Activity) -> int:
    """Queue a delivery to every enabled hook on the project that this change matches.

    Returns how many were queued. Costs one query for a change on a project
    with no hooks, which is every change on most projects, and does nothing at
    all for a verb outside :data:`EVENTS`.
    """
    if entry.project_id is None or entry.verb not in EVENTS:
        return 0
    if "changes" in entry.payload and not entry.payload["changes"]:
        return 0

    hooks = list(
        await session.scalars(
            select(Hook).where(
                Hook.project_id == entry.project_id,
                Hook.enabled.is_(True),
                Hook.manual.is_(False),
            )
        )
    )
    candidates = [hook for hook in hooks if not hook.verbs or entry.verb in hook.verbs]
    if not candidates:
        return 0

    # The change itself may still be pending — a card created in this request
    # has not reached the database — and everything below reads rows.
    await session.flush()
    card = await _card_of(session, entry)
    matched = [hook for hook in candidates if matches(hook, entry.verb, card)]
    if not matched:
        return 0

    body = await event_body(session, entry, card)
    for hook in matched:
        session.add(
            HookDelivery(
                hook_id=hook.id,
                activity_id=entry.id,
                event=entry.verb,
                payload=body,
                next_attempt_at=now(),
            )
        )
    return len(matched)


async def _card_of(session: AsyncSession, entry: Activity) -> Card:
    task = (
        await session.get(Task, entry.entity_id)
        if entry.entity_type == EntityType.TASK and entry.entity_id is not None
        else None
    )
    changes = entry.payload.get("changes") or []
    moved_column = entry.verb == Verb.TASK_MOVED and any(
        change.get("field") == "column" for change in changes
    )
    from_column = entry.payload.get("from_column_id") if moved_column else None
    assignee = await session.get(Person, task.assignee_id) if task is not None else None
    mentioned = (entry.payload.get("mentioned") or []) if entry.verb == Verb.TASK_COMMENTED else []
    return Card(
        task=task,
        moved_column=moved_column,
        from_column_id=UUID(from_column) if from_column else None,
        assigned_to_agent=assignee is not None and assignee.is_agent,
        mentioned=frozenset(UUID(person["id"]) for person in mentioned),
    )


def matches(hook: Hook, verb: str, card: Card) -> bool:
    """Whether one hook fires for one change. See the module docstring for the rules."""
    if hook.verbs and verb not in hook.verbs:
        return False
    filtered = any(
        (
            hook.to_column_id,
            hook.from_column_id,
            hook.template_id,
            hook.task_type,
            hook.agent_only,
            hook.mentions_only,
            hook.mentioned_person_id,
        )
    )
    if not filtered:
        return True

    task = card.task
    if task is None:
        return False
    if hook.template_id is not None and task.template_id != hook.template_id:
        return False
    if hook.task_type is not None and task.type != hook.task_type:
        return False
    if hook.agent_only and not card.assigned_to_agent:
        return False
    if (hook.mentions_only or hook.mentioned_person_id is not None) and (
        verb != Verb.TASK_COMMENTED or not card.mentioned
    ):
        return False
    if hook.mentioned_person_id is not None and hook.mentioned_person_id not in card.mentioned:
        return False
    if hook.from_column_id is not None and (
        not card.moved_column or card.from_column_id != hook.from_column_id
    ):
        return False
    if hook.to_column_id is not None:
        if verb == Verb.TASK_MOVED and not card.moved_column:
            return False
        if task.column_id != hook.to_column_id:
            return False
    return True
