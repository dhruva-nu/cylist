"""Writing the audit trail, and reading one entity's share of it back.

Call :func:`record` from the service that performed the change, never from a
router — that way an action logged once is logged however it was triggered.

Reading is the other half: :func:`for_entity` narrows the trail to one thing,
which is how a card answers "what happened to me". :func:`describe` turns a
verb and its payload into a sentence, in one place, so the board, the CLI and
an agent reading the API are all told the same story in the same words.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.activity import Activity


async def record(
    session: AsyncSession,
    principal: Principal,
    verb: str,
    *,
    entity_type: str,
    entity_id: UUID | None = None,
    project_id: UUID | None = None,
    payload: dict[str, Any] | None = None,
) -> Activity:
    """Append one entry to the audit trail.

    Args:
        verb: Dotted past-tense event name, e.g. ``"token.issued"``.
        entity_type: The kind of thing that changed, e.g. ``"task"``.
        entity_id: Which one, when the entity survives the change.
        project_id: Scopes the entry to a project's feed, when applicable.
        payload: Small, non-secret detail about the change. Never put a
            plaintext credential in here — the audit log is readable with the
            ``read`` scope alone.
    """
    entry = Activity(
        actor_token_id=principal.token_id,
        actor_label=principal.label,
        channel=principal.channel,
        verb=verb,
        entity_type=entity_type,
        entity_id=entity_id,
        project_id=project_id,
        payload=payload or {},
    )
    session.add(entry)
    return entry


async def for_entity(
    session: AsyncSession,
    entity_type: str,
    entity_id: UUID,
    *,
    limit: int = 100,
) -> list[Activity]:
    """One thing's own history, newest first.

    Newest first because a history is read to find out what just happened; the
    beginning of a long-running card is the part you already know.
    """
    return list(
        await session.scalars(
            select(Activity)
            .where(Activity.entity_type == entity_type, Activity.entity_id == entity_id)
            .order_by(Activity.occurred_at.desc(), Activity.id.desc())
            .limit(limit)
        )
    )


def describe(entry: Activity) -> str:
    """One sentence saying what an entry did, with no ids in it.

    Written from the payload rather than from the row it changed, so an entry
    still reads correctly years later — "moved to Review" stays true even after
    the column is renamed, because that is what happened at the time.
    """
    payload = entry.payload
    changes = payload.get("changes") or []

    if entry.verb == "task.created":
        return "Created this task."
    if entry.verb == "task.subtask_created":
        parent = payload.get("parent")
        return f"Split out of {parent}." if parent else "Created as a sub-task."
    if entry.verb == "task.updated":
        if not changes:
            return "Saved with nothing changed."
        return f"Changed the {_listed(str(change['label']) for change in changes)}."
    if entry.verb == "task.moved":
        column = next((change for change in changes if change["field"] == "column"), None)
        if column is None:
            return "Reordered within its column."
        return f"Moved from {column['from']} to {column['to']}."
    if entry.verb == "task.sub_status_moved":
        stage = payload.get("sub_status")
        return f"Sub-status set to {stage}." if stage else "Sub-status moved."
    if entry.verb == "task.status_changed":
        reason = (payload.get("reason") or "").strip()
        label = _STATUS_WORDS.get(str(payload.get("to")), str(payload.get("to")))
        return f"Status {label} — {reason}" if reason else f"Status {label}."
    if entry.verb == "task.commented":
        return "Added a comment."
    if entry.verb == "task.checklist_added":
        return f"Added {_quoted(payload.get('title'))} to the checklist."
    if entry.verb == "task.checklist_updated":
        state = _CHECKLIST_WORDS.get(str(payload.get("state")), "changed")
        return f"{state} {_quoted(payload.get('title'))}."
    if entry.verb == "task.checklist_deleted":
        return f"Removed {_quoted(payload.get('title'))} from the checklist."

    # An entry this function has not been taught about is still worth showing:
    # a history with a gap in it is worse than one worded a little stiffly.
    return entry.verb.split(".")[-1].replace("_", " ").capitalize() + "."


_STATUS_WORDS = {
    "active": "active",
    "hold": "on hold",
    "blocked": "blocked",
    "cancelled": "cancelled",
}

_CHECKLIST_WORDS = {"open": "Reopened", "done": "Ticked off", "cancelled": "Cancelled"}


def _quoted(title: Any) -> str:
    return f"“{title}”" if title else "an item"


def _listed(words: Any) -> str:
    """Join words the way a sentence does: a, b and c."""
    items = list(words)
    if len(items) <= 1:
        return items[0] if items else ""
    return f"{', '.join(items[:-1])} and {items[-1]}"
