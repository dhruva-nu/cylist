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

from sqlalchemy import func, select
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
    limit: int,
    offset: int = 0,
) -> tuple[list[Activity], int]:
    """One page of one thing's own history, newest first, and how many there are.

    Newest first because a history is read to find out what just happened; the
    beginning of a long-running card is the part you already know. Paged
    because the record of a card worked on for a month is longer than anyone
    opening it wants at once, and the total comes back with the page so the
    caller can say how much more there is without asking again.

    An entry that recorded no change to the thing itself is left out: dragging
    a card up its own column, or saving a form without touching a field, is
    not something that happened to the work. Those rows are still in the audit
    feed, which is where "who touched this, and when" is asked; this is where
    "what is different about it now" is.
    """
    where = (
        Activity.entity_type == entity_type,
        Activity.entity_id == entity_id,
        # ``->`` gives SQL NULL for a payload with no ``changes`` at all, which
        # coalesces to "something happened" — an entry written before changes
        # were recorded is not evidence that nothing changed.
        func.coalesce(func.jsonb_array_length(Activity.payload["changes"]), 1) > 0,
    )
    total = await session.scalar(select(func.count()).select_from(Activity).where(*where))
    entries = await session.scalars(
        select(Activity)
        .where(*where)
        .order_by(Activity.occurred_at.desc(), Activity.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(entries), total or 0


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
        if changes:
            return f"Changed the {_listed(str(change['label']) for change in changes)}."
        # Entries written before old and new values were recorded still name
        # the fields a save touched. Older ones than that say only that a save
        # happened, which is worth more than a gap in the record.
        fields = payload.get("fields") or []
        return f"Changed the {_listed(str(field) for field in fields)}." if fields else "Updated."
    if entry.verb == "task.moved":
        column = next((change for change in changes if change["field"] == "column"), None)
        return f"Moved from {column['from']} to {column['to']}." if column else "Moved."
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
