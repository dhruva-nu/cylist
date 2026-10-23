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
* ``agent_only`` — the card's owner, after the change, is the agent. A card
  handed to the agent fires; one taken back from it does not.

Two more are about a comment rather than the card, so a hook with either set
fires on ``task.commented`` and nothing else:

* ``mentions_only`` — the comment tags somebody, ``@Aditi K``.
* ``mentioned_person_id`` — the comment tags this person. Tag the agent in a
  comment and a hook with this set to the agent is how it hears about it.

Who a comment tags is read once, when it is written — see
:mod:`app.services.mentions` — and kept on its activity entry as ``mentioned``.

**A manual hook** fires on none of it. It names no verbs and sets no filters,
and the only event it ever sends is ``hook.fired``, when an admin presses its
button on the Hooks page — :func:`fired_body` is what that sends.
"""

from app.services.hooks.catalogue import (
    CATEGORIES,
    EVENTS,
    QUEUED,
    SECRET_MAX_LENGTH,
    SECRET_MIN_LENGTH,
    SECRET_PREFIX,
    EventCategory,
    EventKind,
)
from app.services.hooks.crud import (
    HookFields,
    HookFilters,
    HookLabels,
    checked_url,
    create,
    delete,
    deliveries,
    delivery,
    get,
    labels_for,
    list_hooks,
    refuse_unless_fireable,
    rotate_secret,
    secret_of,
    update,
)
from app.services.hooks.matching import matches, queue_for
from app.services.hooks.payload import fired_body, sample_body

__all__ = [
    "CATEGORIES",
    "EVENTS",
    "QUEUED",
    "SECRET_MAX_LENGTH",
    "SECRET_MIN_LENGTH",
    "SECRET_PREFIX",
    "EventCategory",
    "EventKind",
    "HookFields",
    "HookFilters",
    "HookLabels",
    "checked_url",
    "create",
    "delete",
    "deliveries",
    "delivery",
    "fired_body",
    "get",
    "labels_for",
    "list_hooks",
    "matches",
    "queue_for",
    "refuse_unless_fireable",
    "rotate_secret",
    "sample_body",
    "secret_of",
    "update",
]
