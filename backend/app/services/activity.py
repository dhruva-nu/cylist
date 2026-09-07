"""Writing the audit trail, and reading one entity's share of it back.

Call :func:`record` from the service that performed the change, never from a
router — that way an action logged once is logged however it was triggered.

Reading is the other half, and it is asked in two shapes. :func:`for_entity`
narrows the trail to one thing, which is how a card answers "what happened to
me"; :func:`between` narrows it to one project over a stretch of time, which is
how a day answers "what did I do". Both hand their rows to :func:`entry_of`,
and :func:`describe` turns a verb and its payload into a sentence, in one
place, so the board, the day's report, the CLI and an agent reading the API are
all told the same story in the same words.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.models.activity import Activity
from app.schemas.activity import FieldChange, HistoryEntry


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
        changed_something(),
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


def changed_something() -> ColumnElement[bool]:
    """Whether an entry altered the thing it names, as a WHERE clause.

    Dragging a card up its own column, or saving a form without touching a
    field, is a request the server handled rather than something that happened
    to the work. Every reader that tells a story — a card's history, a day's
    report — leaves those out; the raw feed at ``/activity``, which answers
    "who touched what", keeps them.

    ``->`` gives SQL NULL for a payload with no ``changes`` at all, which
    coalesces to "something happened": an entry written before old and new
    values were recorded is not evidence that nothing changed.
    """
    return func.coalesce(func.jsonb_array_length(Activity.payload["changes"]), 1) > 0


async def between(
    session: AsyncSession,
    project_id: UUID,
    start: datetime,
    end: datetime,
) -> list[Activity]:
    """Everything that changed one project between two moments, oldest first.

    Half-open — ``start`` included, ``end`` excluded — so consecutive windows
    tile a week without an entry landing in two of them or in neither.

    Oldest first because this is read as a narrative rather than searched for
    the latest thing: a day is recounted in the order it happened.
    """
    entries = await session.scalars(
        select(Activity)
        .where(
            Activity.project_id == project_id,
            Activity.occurred_at >= start,
            Activity.occurred_at < end,
            changed_something(),
        )
        .order_by(Activity.occurred_at, Activity.id)
    )
    return list(entries)


def entry_of(entry: Activity, columns: Mapping[str, str] | None = None) -> HistoryEntry:
    """One audit row as a readable entry: the sentence, and the detail behind it.

    The one place a stored row becomes something to show, so a card's history
    and a day's report cannot drift into wording the same event differently.

    ``columns`` maps a board column's id to its name, and is only consulted for
    entries too old to have recorded names themselves — see :func:`describe`.
    """
    return HistoryEntry(
        id=entry.id,
        occurred_at=entry.occurred_at,
        actor_label=entry.actor_label,
        channel=entry.channel,
        verb=entry.verb,
        summary=describe(entry, columns),
        changes=[
            FieldChange.model_validate(change) for change in entry.payload.get("changes") or []
        ],
        payload=entry.payload,
    )


def moved(before: Any, after: Any) -> str:
    """How a move reads: where the card came from, and where it got to.

    Its own function because a card is moved twice over — once when it happens,
    and once more when a day's report collapses an afternoon of dragging into
    the one move it amounted to. Both say it the same way.

    ``before == after`` only happens to a collapsed move, a card that went
    somewhere and came back; a single move within one column changes nothing
    and is never written down at all.
    """
    if before and after:
        return (
            f"Moved from {before} to {after}."
            if before != after
            else f"Moved out and back to {after}."
        )
    return f"Moved to {after}." if after else "Moved."


def excerpt(text: str, limit: int = 200) -> str:
    """What somebody wrote, short enough to be one line of a report.

    The trail keeps the words themselves, not just the fact that words were
    written — "Added a comment." is a line nobody learns anything from. It
    keeps only the opening of a long one, on one line, because a history and a
    day's note are lists of what happened rather than the conversation itself:
    the comment is still on the card, in full, when that is what is wanted.
    """
    said = " ".join(text.split())
    return said if len(said) <= limit else said[: limit - 1].rstrip() + "…"


def describe(entry: Activity, columns: Mapping[str, str] | None = None) -> str:
    """One sentence saying what an entry did, with no ids in it.

    Written from the payload rather than from the row it changed, so an entry
    still reads correctly years later — "moved to Review" stays true even after
    the column is renamed, because that is what happened at the time.

    ``columns`` is the exception, and only for moves recorded before CYLIST-8:
    those rows kept the destination's *id* and neither column's name, so
    without a board to look the id up in there is nothing to say but "Moved.".
    Passing today's names recovers half the sentence — where the card went —
    at the cost of naming that column as it is called now. Given for a column
    since deleted, or not given at all, the bare sentence stands.

    A task's own events are worded here at length, because they are the ones a
    card's history is made of and the only ones carrying old and new values.
    Everything else a project can do — a file uploaded, a column added, a
    secret revealed — is worded by ``_ELSEWHERE``, which is what a day's report
    needs to say more than "Uploaded.".
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
        # A move into the board's last column finishes the card and a move out
        # of it reopens one, and either is the part of the sentence worth
        # reading — "Moved from Done to In progress." is where it went, not
        # what it now is.
        settled = next((change for change in changes if change["field"] == "finished"), None)
        # Where the last column is divided into sections, which of them the card
        # ended in — the answer to "how", which the column alone stopped being
        # able to give the moment there was more than one way off the board.
        landed = next((change for change in changes if change["field"] == "outcome"), None)
        ended_as = landed["to"] if landed is not None else None
        if settled is None:
            ending = f" Ended as {ended_as}." if ended_as else ""
        elif settled["to"] == "open":
            ending = " Reopened."
        else:
            ending = f" Finished as {ended_as}." if ended_as else " Finished."

        if column is not None:
            return moved(column["from"], column["to"]) + ending
        # A card dragged between two sections of the column it was already in
        # has not moved, so the sentence is the ending on its own.
        if landed is not None:
            return ending.strip()
        return moved(None, (columns or {}).get(str(payload.get("column_id")))) + ending
    if entry.verb == "task.finished":
        # Worded as the sub-task's own line, because that is the card whose
        # history it is written to. The parent it belongs to is named on every
        # other line of that history already.
        return "Finished." if payload.get("finished") else "Reopened."
    if entry.verb == "task.sub_status_moved":
        stage = payload.get("sub_status")
        return f"Sub-status set to {stage}." if stage else "Sub-status moved."
    if entry.verb == "task.status_changed":
        reason = (payload.get("reason") or "").strip()
        label = _STATUS_WORDS.get(str(payload.get("to")), str(payload.get("to")))
        return f"Status {label} — {reason}" if reason else f"Status {label}."
    if entry.verb == "task.commented":
        said = str(payload.get("comment") or "").strip()
        return f"Commented: {_quoted(said)}" if said else "Added a comment."
    if entry.verb == "task.checklist_added":
        return f"Added {_quoted(payload.get('title'))} to the checklist."
    if entry.verb == "task.checklist_updated":
        state = _CHECKLIST_WORDS.get(str(payload.get("state")), "changed")
        return f"{state} {_quoted(payload.get('title'))}."
    if entry.verb == "task.checklist_deleted":
        return f"Removed {_quoted(payload.get('title'))} from the checklist."

    written = _ELSEWHERE.get(entry.verb)
    if written is not None:
        return written(payload)

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


def _worded(kind: Any) -> str:
    """A stored enum value as a word: ``gdrive`` reads as Google Drive."""
    return _WORDS.get(str(kind), str(kind).replace("_", " "))


_WORDS = {
    "sharepoint": "SharePoint",
    "gdrive": "Google Drive",
    "upload": "an upload",
    "other": "elsewhere",
    "branch": "branch",
    "secret": "secret",
    "team": "team member",
    "client": "client",
}

_ELSEWHERE: dict[str, Callable[[dict[str, Any]], str]] = {
    # --- The project itself -------------------------------------------------
    "project.created": lambda p: f"Started the project {p.get('name') or p.get('key')}.",
    "project.updated": lambda p: f"Changed the project's {_fields(p)}.",
    "project.archived": lambda p: "Archived the project.",
    "project.members_changed": lambda p: (
        f"Set the project's membership to {p.get('member_count', 0)} "
        f"{'person' if p.get('member_count') == 1 else 'people'}."
    ),
    # --- The board's columns ------------------------------------------------
    "column.created": lambda p: f"Added the column {_quoted(p.get('name'))}.",
    "column.updated": lambda p: f"Changed a column's {_fields(p)}.",
    "column.deleted": lambda p: f"Deleted the column {_quoted(p.get('name'))}.",
    "column.reordered": lambda p: "Reordered the board's columns.",
    # --- Task templates -------------------------------------------------------
    "template.created": lambda p: (
        f"Added the task template {_quoted(p.get('name'))}, with {p.get('stage_count', 0)} "
        f"{'column' if p.get('stage_count') == 1 else 'columns'} set up."
    ),
    "template.updated": lambda p: f"Changed the task template {_quoted(p.get('name'))}.",
    "template.deleted": lambda p: f"Deleted the task template {_quoted(p.get('name'))}.",
    # --- Files --------------------------------------------------------------
    "folder.created": lambda p: f"Created the folder {_quoted(p.get('name'))}.",
    "folder.updated": lambda p: f"Changed the folder {_quoted(p.get('name'))}'s {_fields(p)}.",
    "folder.deleted": lambda p: f"Deleted the folder {_quoted(p.get('name'))}.",
    "file.uploaded": lambda p: f"Uploaded {_quoted(p.get('name'))}.",
    "link.added": lambda p: f"Linked {_quoted(p.get('name'))} from {_worded(p.get('source'))}.",
    "item.updated": lambda p: f"Changed {_quoted(p.get('name'))}'s {_fields(p)}.",
    "item.deleted": lambda p: f"Deleted {_quoted(p.get('name'))}.",
    # --- The vault. Names and structure only; never a value -----------------
    "vault.tree_created": lambda p: f"Created the vault tree {_quoted(p.get('name'))}.",
    "vault.tree_updated": lambda p: f"Changed the vault tree {_quoted(p.get('name'))}.",
    "vault.tree_deleted": lambda p: f"Deleted the vault tree {_quoted(p.get('name'))}.",
    "vault.node_created": lambda p: (
        f"Added the {_worded(p.get('kind'))} {_quoted(p.get('name'))} to {p.get('tree')}."
    ),
    "vault.node_updated": lambda p: f"Changed the vault entry {_quoted(p.get('name'))}.",
    "vault.node_deleted": lambda p: (
        f"Deleted the {_worded(p.get('kind'))} {_quoted(p.get('name'))} from {p.get('tree')}."
    ),
    "vault.node_moved": lambda p: f"Moved the vault entry {_quoted(p.get('name'))}.",
    "vault.secret_revealed": lambda p: (
        f"Revealed the secret {_quoted(p.get('name'))} in {p.get('tree')}."
    ),
    # --- The directory, tokens, and coming in through the front door --------
    "person.added": lambda p: f"Added {p.get('name')} as a {_worded(p.get('kind'))}.",
    "person.updated": lambda p: f"Changed a person's {_fields(p)}.",
    "person.archived": lambda p: f"Archived {p.get('name')}.",
    "token.issued": lambda p: f"Issued the API token {_quoted(p.get('name'))}.",
    "token.revoked": lambda p: f"Revoked the API token {_quoted(p.get('name'))}.",
    "session.started": lambda p: "Signed in.",
}


def _fields(payload: dict[str, Any]) -> str:
    """The field names an entry recorded, worded: ``due_date`` reads as due date.

    Only the older ``fields`` shape needs this. Anything writing ``changes``
    carries a ``label`` already fit to print, and is worded above.
    """
    fields = payload.get("fields") or []
    listed = _listed(str(field).replace("_", " ") for field in fields)
    return listed or "details"
