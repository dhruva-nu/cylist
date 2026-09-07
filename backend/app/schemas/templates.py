"""Task templates: the rule for where a template's cards go, and what has to
be done in each column before one may leave it."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.board import MAX_OUTCOMES
from app.models.template import NAME_MAX_LENGTH, SUB_STAGE_MAX_COUNT
from app.schemas.common import Schema


def _stripped(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("must not be blank")
    return cleaned


def _clean_labels(value: list[str]) -> list[str]:
    cleaned = [label.strip() for label in value]
    if any(not label for label in cleaned):
        raise ValueError("a sub-stage label must not be blank")
    return cleaned


class StageInput(Schema):
    """One column a template's cards may sit in, and the sub-stages to give
    them there."""

    column_id: UUID
    sub_stage_labels: list[str] = Field(
        default_factory=list,
        max_length=SUB_STAGE_MAX_COUNT,
        description=(
            "The sub-stages a card of this template passes through in this "
            "column, left to right — loaded onto the card's own click-through "
            "progress bar the moment it lands here. May be empty: a column "
            "can be named without asking anything of the card there."
        ),
    )

    allowed_outcomes: list[str] = Field(
        default_factory=list,
        max_length=MAX_OUTCOMES,
        description=(
            "Which of this column's outcomes the template's cards may end on, by name. "
            "Meaningful only on the board's last column, which is the only one with any. "
            "Empty means all of them — a template may say where its cards go without "
            "saying how they are allowed to end."
        ),
    )

    @field_validator("sub_stage_labels")
    @classmethod
    def _clean(cls, value: list[str]) -> list[str]:
        return _clean_labels(value)

    @field_validator("allowed_outcomes")
    @classmethod
    def _clean_outcomes(cls, value: list[str]) -> list[str]:
        cleaned = [label.strip() for label in value]
        if any(not label for label in cleaned):
            raise ValueError("an outcome must not be blank")
        return cleaned


class StageRead(Schema):
    column_id: UUID
    column_name: str = Field(description="So a stage reads without the board's columns beside it.")
    sub_stage_labels: list[str]
    allowed_outcomes: list[str] = Field(
        description="The column's outcomes this template's cards may end on. Empty means all."
    )


class TemplateCreate(Schema):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    description: str = Field(
        default="",
        max_length=500,
        description=(
            "What kind of card this is. Optional — a template is a word on a "
            'card, and "Hotfix" already explains itself.'
        ),
    )
    stages: list[StageInput] = Field(
        default_factory=list,
        description=(
            "One entry per column this template's cards may sit in, naming "
            "the sub-stages a card passes through in each before it may leave. "
            "Empty means unrestricted — a template may gain its rule later."
        ),
    )

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str) -> str:
        return _stripped(value)

    @field_validator("description")
    @classmethod
    def _trim(cls, value: str) -> str:
        return value.strip()


class TemplateUpdate(Schema):
    """Every field optional; omitted fields are left as they are.

    ``stages`` is the exception to "partial": sending it replaces the whole
    set. A template's rule is read as one thing — the stages together are the
    policy — so it is edited and saved as one thing rather than column by
    column.
    """

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    description: str | None = Field(default=None, max_length=500)
    stages: list[StageInput] | None = None

    @field_validator("name")
    @classmethod
    def _strip(cls, value: str | None) -> str | None:
        return None if value is None else _stripped(value)

    @field_validator("description")
    @classmethod
    def _trim(cls, value: str | None) -> str | None:
        return None if value is None else value.strip()


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
