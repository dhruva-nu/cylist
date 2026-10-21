"""A project's hooks, and what became of each event sent to one."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import Field, field_validator

from app.models.hook import NAME_MAX_LENGTH, URL_MAX_LENGTH, DeliveryState
from app.models.task import TaskType
from app.schemas.common import Page, Schema

MESSAGE_MAX_LENGTH = 2000


class HookEvent(Schema):
    verb: str = Field(description="What a hook names, e.g. `task.moved`.")
    label: str = Field(description="How the Hooks page words it.")
    hint: str = Field(description="One line on when it happens. Empty if the label says it.")
    category: str = Field(description="Which group it is offered in, e.g. `moves`.")
    category_name: str = Field(description="That group's name, e.g. `Card moves`.")


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
    manual: bool = Field(
        default=False,
        description=(
            "Fired by hand, from `POST .../fire`, and never by a change. A manual hook "
            "takes no `verbs` and no filters."
        ),
    )
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
    agent_only: bool = Field(
        default=False, description="Only cards assigned to the agent after the change."
    )
    mentions_only: bool = Field(
        default=False,
        description="Only `task.commented`, and only a comment that tags somebody with `@`.",
    )
    mentioned_person_id: UUID | None = Field(
        default=None,
        description="Only `task.commented`, and only a comment that tags this project member.",
    )
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
    manual: bool | None = Field(
        default=None,
        description="Make it manual, or put it back on changes. Clear its events and "
        "filters in the same request to make it manual.",
    )
    verbs: list[str] | None = None
    to_column_id: UUID | None = None
    from_column_id: UUID | None = None
    template_id: UUID | None = None
    task_type: TaskType | None = None
    agent_only: bool | None = Field(
        default=None, description="Only cards assigned to the agent. `null` turns it off."
    )
    mentions_only: bool | None = Field(
        default=None, description="Only comments that tag somebody. `null` turns it off."
    )
    mentioned_person_id: UUID | None = None


class HookFire(Schema):
    message: str | None = Field(
        default=None,
        max_length=MESSAGE_MAX_LENGTH,
        description="Anything the receiver should be told — sent as `detail.message`.",
        examples=["Release 1.4 to staging"],
    )

    @field_validator("message")
    @classmethod
    def _message(cls, value: str | None) -> str | None:
        # Blank is the same as nothing said.
        return (value or "").strip() or None


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
    manual: bool = Field(description="Fired by hand only; never by a change.")
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
    agent_only: bool = Field(description="Only cards assigned to the agent match.")
    mentions_only: bool = Field(description="Only comments that tag somebody match.")
    mentioned_person_id: UUID | None
    mentioned_person_name: str | None = Field(
        description="The tagged person's name now, or null if they have been deleted — in "
        "which case the filter matches nothing."
    )
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


class HookPage(Page[HookRead]):
    """A page of a project's hooks, by name."""


class HookDeliveryPage(Page[HookDeliveryRead]):
    """A page of a hook's deliveries, newest first."""
