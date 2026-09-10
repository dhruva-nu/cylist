"""Agent sessions: what a hook sends, and what a card says back about them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.models.agent_session import AgentSessionReason, AgentSessionState
from app.schemas.common import Schema


class AgentSessionPut(Schema):
    """One report from a harness hook: what the session is doing now.

    Idempotent by design — the same state sent twice is a heartbeat, and the
    hooks send one on every prompt and tool call. Only a *change* of state is
    written to the activity trail.
    """

    state: AgentSessionState
    reason: AgentSessionReason | None = Field(
        default=None,
        description=(
            "Why, for `waiting` (`turn_ended`, `permission`, `idle`, `question`) "
            "and `done` (`session_ended`). Ignored — and cleared — for `working`."
        ),
    )
    client_name: str | None = Field(
        default=None,
        max_length=200,
        description="The session's display name, if the harness knows it.",
    )

    @field_validator("client_name")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AgentSessionRead(Schema):
    id: UUID
    task_id: UUID
    actor_label: str = Field(description="The name of the token the hook reports with.")
    client_session_id: str = Field(description="The harness's own id for the conversation.")
    client_name: str | None = Field(description="The session's display name, if known.")
    state: AgentSessionState
    reason: AgentSessionReason | None
    note: str | None
    started_at: datetime
    state_changed_at: datetime = Field(description="When `state` last changed.")
    last_seen_at: datetime = Field(
        description="When the session last reported in, over its socket or the HTTP route."
    )
    ended_at: datetime | None
    dismissed_at: datetime | None


PresenceState = Literal["working", "waiting", "done"]
"""What a card's border says.

Three, not four. There was a `stale` — a working session gone quiet past a
threshold — for as long as nothing ended the rows of processes that died
without saying so. Something does now, so the board reads a fact instead of
a guess about the clock.
"""


class AgentPresence(Schema):
    """What a card says about the agents on it, reduced to one border.

    One card may carry several sessions; this is the one the board draws, by
    the precedence in :func:`app.services.agent_sessions.presence` — whatever
    needs a human first.
    """

    state: PresenceState
    count: int = Field(
        description=(
            "How many sessions this summarises: the open ones while any is open, "
            "the finished-but-not-dismissed ones once none are."
        )
    )
    reason: AgentSessionReason | None = Field(description="The deciding session's reason.")
    client_name: str | None = Field(description="The deciding session's name.")
    since: datetime = Field(description="When the deciding session entered its state.")
    last_seen_at: datetime = Field(description="When the deciding session last reported in.")
