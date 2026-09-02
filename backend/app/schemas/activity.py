"""The audit feed, and one entry of it read back as a sentence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import ConfigDict, Field

from app.models.activity import Channel
from app.schemas.common import Schema


class ActivityRead(Schema):
    id: UUID
    occurred_at: datetime
    actor_label: str
    channel: Channel
    verb: str
    entity_type: str
    entity_id: UUID | None
    project_id: UUID | None
    payload: dict[str, Any]


class FieldChange(Schema):
    """One field of a record, before and after somebody touched it."""

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)

    field: str = Field(description="The field's name on the record, e.g. `due_date`.")
    label: str = Field(description="How to word it, e.g. `due date`.")
    before: Any = Field(
        default=None,
        alias="from",
        description="What it said before, rendered the way the card renders it — "
        "an assignee by name, a date as `YYYY-MM-DD`. Null if it was not set.",
    )
    after: Any = Field(default=None, alias="to", description="What it says now.")


class HistoryEntry(Schema):
    """One audit entry, worded: what happened, when, and who did it.

    The readable half of :class:`ActivityRead`. ``summary`` is written by the
    server — see :func:`app.services.activity.describe` — so a card's history,
    a day's report, the CLI and an agent all tell the same story in the same
    words rather than each keeping a verb-to-sentence map of its own.
    """

    id: UUID
    occurred_at: datetime
    actor_label: str = Field(
        description="Who did it — a token's name, or `Web session` for the browser."
    )
    channel: Channel = Field(description="`web` if a person did it, `api` if an agent did.")
    verb: str = Field(description="Dotted past-tense event name, e.g. `task.moved`.")
    summary: str = Field(description="The same thing as one readable sentence.")
    changes: list[FieldChange] = Field(
        default_factory=list,
        description="Field-by-field detail, where the event has any. Empty otherwise.",
    )
    payload: dict[str, Any] = Field(description="Everything the entry recorded, unabridged.")
