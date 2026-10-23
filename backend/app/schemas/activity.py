"""The audit feed, and one entry of it read back as a sentence."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from app.models.activity import Channel
from app.schemas.common import Page, Schema
from app.services.readmodels.activity import (
    FieldChange as FieldChange,
)
from app.services.readmodels.activity import (
    HistoryEntry as HistoryEntry,
)


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


class ActivityPage(Page[ActivityRead]):
    """A page of the audit feed, newest first."""
