"""Turning an audit row into a sentence a person can read.

:func:`describe` turns a verb and its payload into words in one place, so the
board, the day's report, the CLI and an agent reading the API are all told the
same story in the same words. :func:`entry_of` wraps that sentence up with the
detail behind it for the readers that show a history.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import Any
from urllib.parse import urlsplit

from app.models.activity import Activity
from app.schemas.activity import FieldChange, HistoryEntry


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
        return _describe_update(payload, changes)
    if entry.verb == "task.moved":
        return _describe_move(payload, changes, columns)
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
    if entry.verb == "task.comment_deleted":
        said = str(payload.get("comment") or "").strip()
        return f"Deleted the comment {_quoted(said)}." if said else "Deleted a comment."
    if entry.verb == "task.archived":
        file = payload.get("file")
        return f"Moved to cold storage, in {file}." if file else "Moved to cold storage."
    if entry.verb == "task.restored":
        column = payload.get("column")
        return (
            f"Restored from cold storage to {column}." if column else "Restored from cold storage."
        )
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


def _describe_update(payload: Mapping[str, Any], changes: list[dict[str, Any]]) -> str:
    """The sentence for a save to a card's fields: which of them it changed.

    Pull requests are the exception, and say what happened to them rather than
    that they changed: "Pull request #212 merged." is the line a day's report
    is read for, and "Changed the pull requests." is the same line whether a
    pull request landed or a typo in its link was fixed.
    """
    if changes:
        pr_change = _change_to(changes, "pr_refs")
        said = _describe_pr_change(pr_change) if pr_change is not None else []
        # A save that left every pull request where it was — one reordered,
        # say — has nothing to report about them but that they changed.
        others = [change for change in changes if change is not pr_change or not said]
        if others:
            said.insert(0, f"Changed the {_listed(str(change['label']) for change in others)}.")
        return " ".join(said)
    # Entries written before old and new values were recorded still name the
    # fields a save touched. Older ones than that say only that a save
    # happened, which is worth more than a gap in the record.
    fields = payload.get("fields") or []
    if not fields:
        return "Updated."
    return f"Changed the {_listed(str(field) for field in fields)}."


def _describe_pr_change(change: Mapping[str, Any]) -> list[str]:
    """A sentence for each pull request a save did something to, in card order.

    Read back from the ``#212 (merged)`` strings the change was recorded as —
    see ``_describe_pr_refs`` in :mod:`app.services.tasks` — matched up by
    link, so a pull request that moved along is told apart from one that was
    swapped for another. Entries written before CYLIST-76 recorded the links
    alone, and still say which were added and which removed.
    """
    was = dict(_pr_ref_read(said) for said in change.get("from") or [])
    now = dict(_pr_ref_read(said) for said in change.get("to") or [])
    said: list[str] = []
    for url, state in now.items():
        name = _pr_named(url)
        if url not in was:
            said.append(_PR_ADDED.get(state or "", "Added pull request {name}.").format(name=name))
        elif state != was[url] and state is not None:
            backwards = _PR_ORDER.index(state) < _PR_ORDER.index(was[url] or "raised")
            said.append(f"Pull request {name} {'back to ' if backwards else ''}{state}.")
    said += [f"Removed pull request {_pr_named(url)}." for url in was if url not in now]
    return said


_PR_ORDER = ["raised", "approved", "merged"]
"""How far a pull request has got, nearest to landed last — see ``PrState``."""

_PR_ADDED = {
    "raised": "Raised pull request {name}.",
    "approved": "Added pull request {name}, already approved.",
    "merged": "Added pull request {name}, already merged.",
}

_PR_REF_SAID = re.compile(r"(?P<url>.+) \((?P<state>raised|approved|merged)\)")


def _pr_ref_read(said: Any) -> tuple[str, str | None]:
    """A recorded ``url (state)`` back as its two halves; a bare link has no state."""
    matched = _PR_REF_SAID.fullmatch(str(said))
    return (matched["url"], matched["state"]) if matched else (str(said), None)


def _pr_named(url: str) -> str:
    """A pull request the way the card names it: ``#212``.

    The same reading as ``prRefLabel`` on the frontend, so the history and the
    card call one pull request the same thing. Anything not a link is already
    what somebody chose to call it.
    """
    if not re.match(r"https?://", url, re.IGNORECASE):
        return url
    parts = urlsplit(url)
    # GitHub and Bitbucket say `pull`/`pull-requests`, GitLab `merge_requests`.
    number = re.search(r"/(?:pull|pull-requests|merge_requests)/(\d+)", parts.path, re.IGNORECASE)
    if number:
        return f"#{number[1]}"
    return next((part for part in reversed(parts.path.split("/")) if part), parts.hostname or url)


def _describe_move(
    payload: Mapping[str, Any],
    changes: list[dict[str, Any]],
    columns: Mapping[str, str] | None,
) -> str:
    """The sentence for a card moving: where it went, and how it ended if it did."""
    column_change = _change_to(changes, "column")
    outcome_change = _change_to(changes, "outcome")
    ending = _move_ending(_change_to(changes, "finished"), outcome_change)

    if column_change is not None:
        return moved(column_change["from"], column_change["to"]) + ending
    # A card dragged between two sections of the column it was already in has
    # not moved, so the sentence is the ending on its own.
    if outcome_change is not None:
        return ending.strip()
    return moved(None, (columns or {}).get(str(payload.get("column_id")))) + ending


def _move_ending(
    finished_change: dict[str, Any] | None, outcome_change: dict[str, Any] | None
) -> str:
    """What a move did to whether the card is done, as the end of its sentence.

    A move into the board's last column finishes the card and a move out of it
    reopens one, and either is the part of the sentence worth reading — "Moved
    from Done to In progress." is where it went, not what it now is.

    Where the last column is divided into sections, which of them the card
    ended in is the answer to "how", which the column alone stopped being able
    to give the moment there was more than one way off the board.
    """
    ended_as = outcome_change["to"] if outcome_change is not None else None
    if finished_change is None:
        return f" Ended as {ended_as}." if ended_as else ""
    if finished_change["to"] == "open":
        return " Reopened."
    return f" Finished as {ended_as}." if ended_as else " Finished."


def _change_to(changes: list[dict[str, Any]], field: str) -> dict[str, Any] | None:
    """The one entry in a list of changes about ``field``, if there is one."""
    return next((change for change in changes if change["field"] == field), None)


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

_AGENT_WAITING = {
    "turn_ended": "The agent finished its turn and is waiting for your reply.",
    "permission": "The agent is waiting for permission to run something.",
    "idle": "The agent has been waiting for a prompt for a while.",
    "question": "The agent asked you a question.",
}

_ELSEWHERE: dict[str, Callable[[dict[str, Any]], str]] = {
    # --- The project itself -------------------------------------------------
    "project.created": lambda p: f"Started the project {p.get('name') or p.get('key')}.",
    "project.updated": lambda p: f"Changed the project's {_worded_fields(p)}.",
    "project.archived": lambda p: "Archived the project.",
    "project.restored": lambda p: "Brought the project back out of the archive.",
    "project.deleted": lambda p: f"Deleted the project {p.get('name') or p.get('key')}.",
    "project.members_changed": lambda p: _membership_sentence(p),
    # --- Roles, and who wears them ------------------------------------------
    "role.created": lambda p: f"Added the role {_quoted(p.get('name'))}.",
    "role.updated": lambda p: (
        f"Renamed the role {_quoted(p.get('was'))} to {_quoted(p.get('name'))}."
        if p.get("was")
        else f"Changed the role {_quoted(p.get('name'))}'s {_worded_fields(p)}."
    ),
    "role.deleted": lambda p: f"Deleted the role {_quoted(p.get('name'))}.",
    "member.role_set": lambda p: (
        f"Made {p.get('person')} {_quoted(p.get('role'))} on this project."
        if p.get("role")
        else f"Took {p.get('person')}'s role off."
    ),
    # --- The board's columns ------------------------------------------------
    "column.created": lambda p: f"Added the column {_quoted(p.get('name'))}.",
    "column.updated": lambda p: f"Changed a column's {_worded_fields(p)}.",
    "column.deleted": lambda p: f"Deleted the column {_quoted(p.get('name'))}.",
    "column.reordered": lambda p: "Reordered the board's columns.",
    # --- Task templates -------------------------------------------------------
    "template.created": lambda p: (
        f"Added the task template {_quoted(p.get('name'))}, with {p.get('stage_count', 0)} "
        f"{'column' if p.get('stage_count') == 1 else 'columns'} set up."
    ),
    "template.updated": lambda p: f"Changed the task template {_quoted(p.get('name'))}.",
    "template.deleted": lambda p: f"Deleted the task template {_quoted(p.get('name'))}.",
    # --- Hooks. Never the secret, which is not in the payload to begin with ---
    "hook.created": lambda p: f"Added the hook {_quoted(p.get('name'))}.",
    "hook.updated": lambda p: f"Changed the hook {_quoted(p.get('name'))}'s {_worded_fields(p)}.",
    "hook.secret_rotated": lambda p: (
        f"Gave the hook {_quoted(p.get('name'))} a new signing secret."
    ),
    "hook.deleted": lambda p: f"Deleted the hook {_quoted(p.get('name'))}.",
    "hook.fired": lambda p: f"Fired the hook {_quoted(p.get('name'))}.",
    # --- Files --------------------------------------------------------------
    "folder.created": lambda p: f"Created the folder {_quoted(p.get('name'))}.",
    "folder.updated": lambda p: (
        f"Changed the folder {_quoted(p.get('name'))}'s {_worded_fields(p)}."
    ),
    "folder.deleted": lambda p: f"Deleted the folder {_quoted(p.get('name'))}.",
    "file.uploaded": lambda p: f"Uploaded {_quoted(p.get('name'))}.",
    "link.added": lambda p: f"Linked {_quoted(p.get('name'))} from {_worded(p.get('source'))}.",
    "item.updated": lambda p: f"Changed {_quoted(p.get('name'))}'s {_worded_fields(p)}.",
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
    "person.updated": lambda p: f"Changed a person's {_worded_fields(p)}.",
    "person.archived": lambda p: f"Archived {p.get('name')}.",
    "token.issued": lambda p: f"Issued the API token {_quoted(p.get('name'))}.",
    "token.revoked": lambda p: f"Revoked the API token {_quoted(p.get('name'))}.",
    "session.started": lambda p: "Signed in.",
    # --- Agents on a card. Written by the harness's hooks, one line per change
    #     of state and never per heartbeat --------------------------------------
    "agent_session.started": lambda p: (
        f"An agent started working on this card ({p.get('client_name')})."
        if p.get("client_name")
        else "An agent started working on this card."
    ),
    "agent_session.waiting": lambda p: (
        f"The agent asked: {p.get('question')}"
        if p.get("reason") == "question" and p.get("question")
        else _AGENT_WAITING.get(str(p.get("reason")), "The agent is waiting on you.")
    ),
    "agent_session.answered": lambda p: (
        f"Answered the agent's permission request ({p.get('text')}): {p.get('answer')}."
        if p.get("kind") == "permission"
        else f"Answered the agent's question {_quoted(p.get('text'))}: {p.get('answer')}"
    ),
    "agent_session.unanswered": lambda p: (
        "Nobody answered the agent's request in time; it was left to the terminal."
        if p.get("kind") == "permission"
        else f"Nobody answered the agent's question {_quoted(p.get('text'))} in time."
    ),
    "agent_session.finished": lambda p: (
        f"The agent moved on to {p.get('moved_to')}."
        if p.get("reason") == "moved" and p.get("moved_to")
        else "The agent's session ended."
    ),
}


def _worded_fields(payload: dict[str, Any]) -> str:
    """The field names an entry recorded, worded: ``due_date`` reads as due date.

    Only the older ``fields`` shape needs this. Anything writing ``changes``
    carries a ``label`` already fit to print, and is worded above.
    """
    fields = payload.get("fields") or []
    listed = _listed(str(field).replace("_", " ") for field in fields)
    return listed or "details"


def _membership_sentence(payload: dict[str, Any]) -> str:
    """Who a membership save added and took off, or its count for older rows.

    Entries written before CYLIST-116 carry only ``member_count``, and still
    read the way they always did. Names are what make a later one worth
    reading: "took Ravi off" is the line somebody scanning the day for a lost
    admin is looking for.
    """
    joined, left = payload.get("joined"), payload.get("left")
    if joined is None and left is None:
        count = payload.get("member_count", 0)
        return f"Set the project's membership to {count} {'person' if count == 1 else 'people'}."

    said = []
    if joined:
        said.append(f"added {_listed(joined)}")
    if left:
        said.append(f"took {_listed(left)} off")
    if not said:
        return "Saved the project's membership unchanged."
    sentence = " and ".join(said)
    return f"{sentence[0].upper()}{sentence[1:]}."
