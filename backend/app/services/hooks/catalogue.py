"""The events a hook can be told about, in the words the Hooks page offers them."""

from __future__ import annotations

from dataclasses import dataclass

from app.models.activity import Verb


@dataclass(frozen=True)
class EventKind:
    """One verb a hook may name, and how the Hooks page says it."""

    verb: str
    label: str
    hint: str = ""
    """One line on when it happens, for the picker. Empty where the label
    already says everything."""


@dataclass(frozen=True)
class EventCategory:
    """A group of verbs the Hooks page offers together: "Card moves"."""

    id: str
    name: str
    events: tuple[EventKind, ...]


CATEGORIES: tuple[EventCategory, ...] = (
    EventCategory(
        "moves",
        "Card moves",
        (
            EventKind(
                Verb.TASK_MOVED,
                "Moved to another column",
                "A card lands somewhere new — the one deploys hang off.",
            ),
            EventKind(
                Verb.TASK_SUB_STATUS_MOVED,
                "Sub-status stepped",
                "Drafted → Reviewed, inside one column.",
            ),
        ),
    ),
    EventCategory(
        "cards",
        "Card changes",
        (
            EventKind(Verb.TASK_CREATED, "Card created", "A new card on the board."),
            EventKind(
                Verb.TASK_UPDATED, "Card edited", "Title, description, owner, dates or priority."
            ),
            EventKind(
                Verb.TASK_STATUS_CHANGED,
                "Put on hold, blocked or cancelled",
                "Or picked back up — with the reason given.",
            ),
            EventKind(
                Verb.TASK_DELETED, "Card deleted", "Only the reference is sent; the card is gone."
            ),
            EventKind(
                Verb.TASK_ARCHIVED,
                "Moved to cold storage",
                "Taken off the board with the rest of its last column, into a CSV.",
            ),
            EventKind(Verb.TASK_RESTORED, "Restored from cold storage", "Back in the last column."),
        ),
    ),
    EventCategory(
        "comments",
        "Comments",
        (
            EventKind(
                Verb.TASK_COMMENTED,
                "Comment added",
                "The comment's text, and who it tags, are in the payload.",
            ),
        ),
    ),
    EventCategory(
        "work",
        "Sub-tasks & checklists",
        (
            EventKind(Verb.TASK_SUBTASK_CREATED, "Sub-task created", "Split out of a card."),
            EventKind(Verb.TASK_FINISHED, "Sub-task finished", "Or reopened."),
            EventKind(Verb.TASK_CHECKLIST_ADDED, "Checklist item added"),
            EventKind(
                Verb.TASK_CHECKLIST_UPDATED, "Checklist item ticked", "Or cancelled, or reopened."
            ),
            EventKind(Verb.TASK_CHECKLIST_DELETED, "Checklist item removed"),
        ),
    ),
    EventCategory(
        "board",
        "Board setup",
        (
            EventKind(Verb.COLUMN_CREATED, "Column added"),
            EventKind(
                Verb.COLUMN_UPDATED, "Column changed", "Renamed, re-described or re-sectioned."
            ),
            EventKind(Verb.COLUMN_DELETED, "Column deleted"),
            EventKind(Verb.COLUMN_REORDERED, "Columns reordered"),
            EventKind(Verb.TEMPLATE_CREATED, "Template added"),
            EventKind(Verb.TEMPLATE_UPDATED, "Template changed"),
            EventKind(Verb.TEMPLATE_DELETED, "Template deleted"),
        ),
    ),
    EventCategory(
        "goals",
        "Goals",
        (
            EventKind(Verb.GOAL_CREATED, "Goal added"),
            EventKind(Verb.GOAL_UPDATED, "Goal changed", "Renamed, re-dated or marked achieved."),
            EventKind(Verb.GOAL_DELETED, "Goal deleted"),
        ),
    ),
)
"""Every verb a hook may name, grouped the way the Hooks page offers them.

The board's own changes and nothing else. The trail records more — a vault
secret revealed, a token issued — but those are about who may do what, not
about the work, and a URL outside the deployment is the last place their
existence should be announced.
"""

EVENTS: dict[str, str] = {
    kind.verb: kind.label for category in CATEGORIES for kind in category.events
}
"""The same verbs, flat, in the same order: what matching and validation ask."""

SECRET_PREFIX = "whsec_"  # noqa: S105 - a prefix, not a secret
SECRET_MIN_LENGTH = 16
SECRET_MAX_LENGTH = 200
