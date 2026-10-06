"""Hooks: which changes on a board are worth telling the outside world, and to whom.

Two halves. :func:`queue_for` is called by :func:`app.services.activity.record`
for every change it writes, and turns a change that some hook on the project
matches into one ``hook_delivery`` row per hook — in the same transaction, so a
change that rolls back queues nothing. Sending is
:mod:`app.services.hook_delivery`'s business.

The rest is the rules themselves: a project admin's to make, change and
delete, each with a URL and a signing secret only it and its receiver know.

**What fires.** Every verb in :data:`EVENTS`, which is every change a board
records — a card created, moved, edited, commented on, put on hold; a column
added; a goal changed. A row that recorded no change to the thing it names — a
card dragged up its own column — fires nothing, for the reason
:func:`app.services.activity.changed_something` leaves it out of a history.

**What a filter means.** Every filter is about the card, so a hook with any
filter set only ever fires for a card's events:

* ``to_column_id`` — the column the card is in after the change. For a move,
  only a move that changed column matches: a card dragged between two sections
  of In staging has not arrived in In staging again.
* ``from_column_id`` — only a move out of that column.
* ``template_id``, ``task_type`` — the card's template and type.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now
from app.core.crypto import HOOK_SECRET_DOMAIN, KEY_VERSION, VaultCipher
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.ids import uuid7
from app.models.activity import Activity
from app.models.board import BoardColumn
from app.models.hook import Hook, HookDelivery
from app.models.person import Person
from app.models.project import Project
from app.models.task import Task, TaskType
from app.models.template import TaskTemplate

EVENTS: dict[str, str] = {
    "task.created": "Card created",
    "task.subtask_created": "Sub-task created",
    "task.moved": "Card moved",
    "task.updated": "Card edited",
    "task.status_changed": "Put on hold, blocked, cancelled or resumed",
    "task.sub_status_moved": "Sub-status moved",
    "task.finished": "Sub-task finished or reopened",
    "task.commented": "Comment added",
    "task.checklist_added": "Checklist item added",
    "task.checklist_updated": "Checklist item ticked or changed",
    "task.checklist_deleted": "Checklist item removed",
    "task.deleted": "Card deleted",
    "column.created": "Column added",
    "column.updated": "Column changed",
    "column.deleted": "Column deleted",
    "column.reordered": "Columns reordered",
    "template.created": "Template added",
    "template.updated": "Template changed",
    "template.deleted": "Template deleted",
    "goal.created": "Goal added",
    "goal.updated": "Goal changed",
    "goal.deleted": "Goal deleted",
}
"""Every verb a hook may name, with the words the Hooks page shows for it.

The board's own changes and nothing else. The trail records more — a vault
secret revealed, a token issued — but those are about who may do what, not
about the work, and a URL outside the deployment is the last place their
existence should be announced.
"""

QUEUED = "hooks.queued"
"""The outbox event :func:`app.services.activity.record` publishes when this
transaction queued deliveries, so the courier wakes once it has committed."""

SECRET_PREFIX = "whsec_"  # noqa: S105 - a prefix, not a secret
SECRET_MIN_LENGTH = 16
SECRET_MAX_LENGTH = 200


# --- Matching a change -----------------------------------------------------


@dataclass(frozen=True)
class _Card:
    """What a change knew about the card it was made to, looked up once."""

    task: Task | None
    moved_column: bool
    """A move that changed column — not a reorder, not a change of section."""
    from_column_id: UUID | None


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
            select(Hook).where(Hook.project_id == entry.project_id, Hook.enabled.is_(True))
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

    body = await _event_body(session, entry, card)
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


async def _card_of(session: AsyncSession, entry: Activity) -> _Card:
    task = (
        await session.get(Task, entry.entity_id)
        if entry.entity_type == "task" and entry.entity_id is not None
        else None
    )
    changes = entry.payload.get("changes") or []
    moved_column = entry.verb == "task.moved" and any(
        change.get("field") == "column" for change in changes
    )
    from_column = entry.payload.get("from_column_id") if moved_column else None
    return _Card(
        task=task,
        moved_column=moved_column,
        from_column_id=UUID(from_column) if from_column else None,
    )


def matches(hook: Hook, verb: str, card: _Card) -> bool:
    """Whether one hook fires for one change. See the module docstring for the rules."""
    if hook.verbs and verb not in hook.verbs:
        return False
    filtered = any((hook.to_column_id, hook.from_column_id, hook.template_id, hook.task_type))
    if not filtered:
        return True

    task = card.task
    if task is None:
        return False
    if hook.template_id is not None and task.template_id != hook.template_id:
        return False
    if hook.task_type is not None and task.type != hook.task_type:
        return False
    if hook.from_column_id is not None and (
        not card.moved_column or card.from_column_id != hook.from_column_id
    ):
        return False
    if hook.to_column_id is not None:
        if verb == "task.moved" and not card.moved_column:
            return False
        if task.column_id != hook.to_column_id:
            return False
    return True


async def _event_body(session: AsyncSession, entry: Activity, card: _Card) -> dict[str, Any]:
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
    if task is None and entry.entity_type == "task":
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


# --- The rules themselves --------------------------------------------------


@dataclass(frozen=True)
class HookFields:
    """What an update may set. ``None`` leaves a field alone; to clear a
    filter, :class:`HookFilters` says so explicitly."""

    name: str | None = None
    enabled: bool | None = None
    verbs: list[str] | None = None
    url: str | None = None


@dataclass(frozen=True)
class HookFilters:
    """The filters an update sets, each one only if it is named in ``given``."""

    given: frozenset[str]
    to_column_id: UUID | None = None
    from_column_id: UUID | None = None
    template_id: UUID | None = None
    task_type: TaskType | None = None


async def list_hooks(session: AsyncSession, project: Project) -> list[Hook]:
    found = await session.scalars(
        select(Hook).where(Hook.project_id == project.id).order_by(Hook.name)
    )
    return list(found)


async def get(session: AsyncSession, project: Project, hook_id: UUID) -> Hook:
    hook = await session.get(Hook, hook_id)
    if hook is None or hook.project_id != project.id:
        raise NotFoundError(
            f"{project.key} has no hook {hook_id}.",
            details={"project_key": project.key, "hook_id": str(hook_id)},
        )
    return hook


async def create(
    session: AsyncSession,
    project: Project,
    cipher: VaultCipher,
    *,
    name: str,
    url: str,
    enabled: bool,
    verbs: list[str],
    filters: HookFilters,
    secret: str | None,
) -> tuple[Hook, str]:
    """Make a hook, and hand back its signing secret — the only time it is shown
    unasked. Generated unless the caller brought one."""
    await _refuse_taken_name(session, project, name)
    # The id is drawn here rather than at the flush, because the secret is
    # sealed against it and the column holding it is NOT NULL.
    hook = Hook(
        id=uuid7(),
        project_id=project.id,
        name=name,
        enabled=enabled,
        verbs=_checked_verbs(verbs),
        url=checked_url(url),
    )
    await _apply_filters(session, project, hook, filters)
    plaintext = _seal(cipher, hook, secret)
    session.add(hook)
    return hook, plaintext


async def update(
    session: AsyncSession,
    project: Project,
    hook: Hook,
    fields: HookFields,
    filters: HookFilters,
) -> list[str]:
    """Change a hook. Returns the names of the fields that changed."""
    changed: list[str] = []
    if fields.name is not None and fields.name != hook.name:
        await _refuse_taken_name(session, project, fields.name)
        hook.name = fields.name
        changed.append("name")
    if fields.enabled is not None and fields.enabled != hook.enabled:
        hook.enabled = fields.enabled
        changed.append("enabled")
    if fields.verbs is not None:
        verbs = _checked_verbs(fields.verbs)
        if verbs != list(hook.verbs):
            hook.verbs = verbs
            changed.append("events")
    if fields.url is not None:
        url = checked_url(fields.url)
        if url != hook.url:
            hook.url = url
            changed.append("url")
    changed += await _apply_filters(session, project, hook, filters)
    return changed


async def delete(session: AsyncSession, hook: Hook) -> None:
    await session.delete(hook)


def rotate_secret(cipher: VaultCipher, hook: Hook, secret: str | None = None) -> str:
    """Give a hook a new signing secret, returning it. The old one stops
    verifying for every delivery sent from now on, including retries."""
    return _seal(cipher, hook, secret)


def secret_of(cipher: VaultCipher, hook: Hook) -> str:
    return cipher.open(
        hook.secret_ciphertext,
        node_id=hook.id,
        key_version=hook.key_version,
        domain=HOOK_SECRET_DOMAIN,
    )


async def deliveries(session: AsyncSession, hook: Hook, *, limit: int) -> list[HookDelivery]:
    found = await session.scalars(
        select(HookDelivery)
        .where(HookDelivery.hook_id == hook.id)
        .order_by(HookDelivery.created_at.desc(), HookDelivery.id.desc())
        .limit(limit)
    )
    return list(found)


async def delivery(session: AsyncSession, hook: Hook, delivery_id: UUID) -> HookDelivery:
    found = await session.get(HookDelivery, delivery_id)
    if found is None or found.hook_id != hook.id:
        raise NotFoundError(
            f"The hook {hook.name!r} has no delivery {delivery_id}.",
            details={"hook_id": str(hook.id), "delivery_id": str(delivery_id)},
        )
    return found


def sample_body(project: Project, hook: Hook, principal: Principal) -> dict[str, Any]:
    """What "Send test" sends: the envelope every event has, about nothing.

    Shaped like a real event so a receiver can be written against it, and
    named ``hook.test`` so it can tell the difference."""
    return {
        "id": None,
        "event": "hook.test",
        "summary": f"A test from the hook {hook.name!r}.",
        "occurred_at": now().isoformat(),
        "project": {"id": str(project.id), "key": project.key, "name": project.name},
        "actor": {
            "name": principal.label,
            "person_id": str(principal.person_id) if principal.person_id else None,
            "channel": str(principal.channel),
        },
        "task": None,
        "changes": [],
        "detail": {},
    }


def checked_url(url: str) -> str:
    """An absolute ``http``/``https`` URL, or a 422 saying what is wrong with it."""
    url = url.strip()
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise UnprocessableRequestError(
            "A hook's URL must be an absolute http:// or https:// address.",
            details={"url": url},
        )
    return url


def _checked_verbs(verbs: list[str]) -> list[str]:
    unknown = sorted(set(verbs) - EVENTS.keys())
    if unknown:
        raise UnprocessableRequestError(
            f"Hooks cannot fire on {', '.join(unknown)}.",
            details={"unknown": unknown, "known": sorted(EVENTS)},
        )
    # Kept in catalogue order, so the same set always reads the same way.
    return [verb for verb in EVENTS if verb in verbs]


async def _apply_filters(
    session: AsyncSession, project: Project, hook: Hook, filters: HookFilters
) -> list[str]:
    changed: list[str] = []
    for field in ("to_column_id", "from_column_id"):
        if field in filters.given:
            value = getattr(filters, field)
            if value is not None:
                await _own_column(session, project, value)
            if getattr(hook, field) != value:
                setattr(hook, field, value)
                changed.append(field)
    if "template_id" in filters.given:
        if filters.template_id is not None:
            await _own_template(session, project, filters.template_id)
        if hook.template_id != filters.template_id:
            hook.template_id = filters.template_id
            changed.append("template_id")
    if "task_type" in filters.given and hook.task_type != filters.task_type:
        hook.task_type = filters.task_type
        changed.append("task_type")
    return changed


async def _own_column(session: AsyncSession, project: Project, column_id: UUID) -> None:
    column = await session.get(BoardColumn, column_id)
    if column is None or column.project_id != project.id:
        raise UnprocessableRequestError(
            f"That column is not on {project.key}'s board.",
            details={"column_id": str(column_id)},
        )


async def _own_template(session: AsyncSession, project: Project, template_id: UUID) -> None:
    template = await session.get(TaskTemplate, template_id)
    if template is None or template.project_id != project.id:
        raise UnprocessableRequestError(
            f"That template is not one of {project.key}'s.",
            details={"template_id": str(template_id)},
        )


async def _refuse_taken_name(session: AsyncSession, project: Project, name: str) -> None:
    taken = await session.scalar(
        select(Hook.id).where(Hook.project_id == project.id, Hook.name == name)
    )
    if taken is not None:
        raise ConflictError(
            f"{project.key} already has a hook called {name!r}.",
            details={"name": name},
        )


def _seal(cipher: VaultCipher, hook: Hook, secret: str | None) -> str:
    plaintext = secret.strip() if secret else SECRET_PREFIX + secrets.token_urlsafe(32)
    if not SECRET_MIN_LENGTH <= len(plaintext) <= SECRET_MAX_LENGTH:
        raise UnprocessableRequestError(
            f"A signing secret must be {SECRET_MIN_LENGTH} to {SECRET_MAX_LENGTH} characters, "
            "or left out to have one generated.",
        )
    hook.secret_ciphertext = cipher.seal(plaintext, node_id=hook.id, domain=HOOK_SECRET_DOMAIN)
    hook.key_version = KEY_VERSION
    hook.secret_hint = plaintext[-4:]
    return plaintext
