"""Task templates: the rule for where a template's cards go, and what has to
be done in each column before one may leave it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.schemas.common import Page, Schema
from app.services.commands.templates import (
    OutcomeLabel as OutcomeLabel,
)
from app.services.commands.templates import (
    StageInput as StageInput,
)
from app.services.commands.templates import (
    SubStageLabel as SubStageLabel,
)
from app.services.commands.templates import (
    TemplateCreate as TemplateCreate,
)
from app.services.commands.templates import (
    TemplateUpdate as TemplateUpdate,
)
from app.services.commands.templates import (
    _clean_labels as _clean_labels,
)
from app.services.commands.templates import (
    _stripped as _stripped,
)


class StageRead(Schema):
    column_id: UUID
    column_name: str = Field(description="So a stage reads without the board's columns beside it.")
    sub_stage_labels: list[str]
    allowed_outcomes: list[str] = Field(
        description="The column's outcomes this template's cards may end on. Empty means all."
    )


class TemplateRead(Schema):
    id: UUID
    project_id: UUID
    name: str
    description: str
    stages: list[StageRead] = Field(description="In the board's own left-to-right order.")
    allowed_column_ids: list[UUID] = Field(
        description=(
            "The stages' column ids, in the same order — a convenience for a "
            "client that only needs to know where a card may go, not what "
            "sub-stages it passes through there. **Empty means unrestricted**: "
            "this template has no stages yet, so its cards go anywhere on the "
            "board."
        )
    )
    task_count: int = Field(description="How many cards were created from this template.")
    created_at: datetime


class TemplatePage(Page[TemplateRead]):
    """A page of a project's task templates, in name order."""
