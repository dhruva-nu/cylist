"""A project's hooks, and what became of each event sent to one."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.hook import NAME_MAX_LENGTH, URL_MAX_LENGTH, DeliveryState
from app.models.task import TaskType
from app.schemas.common import Schema


class HookEvent(Schema):
    verb: str = Field(description="What a hook names, e.g. `task.moved`.")
    label: str = Field(description="How the Hooks page words it.")


class _HookBody(Schema):
    @field_validator("name", check_fields=False)
    @classmethod
    def _name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        if not value:
            raise ValueError("A hook needs a name.")
        return value


class HookCreate(_HookBody):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH, examples=["Hotfix to staging"])
    url: str = Field(
        min_length=1,
        max_length=URL_MAX_LENGTH,
        description="Where events are POSTed. http:// or https://.",
        examples=["https://ci.example.com/cylist"],
    )
    enabled: bool = True
    verbs: list[str] = Field(
        default_factory=list,
        description="The events to fire on, from `GET /hooks/events`. Empty fires on all of them.",
        examples=[["task.moved"]],
    )
    to_column_id: UUID | None = Field(
        default=None,
        description=(
            "Only cards in this column after the change. On `task.moved`, only a move "
            "into it from another column."
        ),
    )
    from_column_id: UUID | None = Field(
        default=None, description="Only `task.moved`, and only out of this column."
    )
    template_id: UUID | None = Field(default=None, description="Only cards of this template.")
    task_type: TaskType | None = Field(default=None, description="Only cards of this type.")
    secret: str | None = Field(
        default=None,
        description="The signing secret to use. Omit to have one generated.",
    )


class HookUpdate(_HookBody):
    """Every field optional. A filter sent as `null` is cleared; one left out is
    left alone."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    url: str | None = Field(default=None, min_length=1, max_length=URL_MAX_LENGTH)
    enabled: bool | None = None
    verbs: list[str] | None = None
    to_column_id: UUID | None = None
    from_column_id: UUID | None = None
    template_id: UUID | None = None
    task_type: TaskType | None = None


class HookSecretRotate(Schema):
    secret: str | None = Field(
        default=None, description="The new signing secret. Omit to have one generated."
    )


class DeliverySummary(Schema):
    state: DeliveryState
    event: str
    created_at: datetime
    last_status_code: int | None


class HookRead(Schema):
    id: UUID
    name: str
    enabled: bool
    url: str
    verbs: list[str]
    to_column_id: UUID | None
    to_column_name: str | None = Field(
        description="The column's name now, or null if it has been deleted — in which "
        "case the filter matches nothing."
    )
    from_column_id: UUID | None
    from_column_name: str | None
    template_id: UUID | None
    template_name: str | None
    task_type: TaskType | None
    secret_hint: str = Field(description="The signing secret's last four characters.")
    last_delivery: DeliverySummary | None
    created_at: datetime
    updated_at: datetime


class HookCreated(HookRead):
    secret: str = Field(description="The signing secret. Shown here and when rotated, never again.")


class HookSecret(Schema):
    secret: str
    secret_hint: str


class DeliveryAttempt(Schema):
    at: datetime
    status_code: int | None
    error: str | None
    duration_ms: int


class HookDeliveryRead(Schema):
    id: UUID
    hook_id: UUID
    activity_id: UUID | None
    event: str
    state: DeliveryState
    attempt_count: int
    next_attempt_at: datetime | None
    delivered_at: datetime | None
    last_status_code: int | None
    last_error: str | None
    attempts: list[DeliveryAttempt]
    payload: dict[str, Any] = Field(description="Exactly the body sent.")
    created_at: datetime
