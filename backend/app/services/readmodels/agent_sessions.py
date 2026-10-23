"""What the agent sessions services return.

Plain values the services build and hand back. The HTTP layer re-exports them
(``app.schemas.agent_sessions``) as the responses its routes declare, so there is one definition
and the services import nothing from the HTTP layer.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field

from app.models.agent_session import (
    AgentFeedKind,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSessionReason,
    AgentSessionState,
)
from app.services.commands.agent_sessions import AgentPromptOption as AgentPromptOption
from app.services.readmodels.base import ReadModel


class AgentPromptRead(ReadModel):
    """Something an agent session is waiting on a person for, or was.

    A `question` the agent asked with `ask_human`, or a `permission` Claude
    Code wants before a tool runs. While `status` is `open` the agent is held
    until somebody answers; afterwards `answer` is what they said — what a
    card shows after "You:".
    """

    id: UUID
    agent_session_id: UUID
    task_id: UUID
    kind: AgentPromptKind
    text: str = Field(
        description="The question, or for a permission what the tool wants to do: "
        "'Run git push origin staging'."
    )
    tool: str | None = Field(description="For a permission, the tool's own name: `Bash`.")
    options: list[AgentPromptOption] = Field(
        description="The choices, in order. For a permission always `allow_once`, "
        "`allow_session` and `deny`; for a question whatever the agent offered, possibly none."
    )
    allows_reply: bool = Field(
        description="Whether a typed reply is accepted as well as a choice. True for a "
        "question, false for a permission."
    )
    owner_id: UUID | None = Field(
        description="Whose agent is asking: the person the session belongs to. A "
        "`permission` may be answered by this person alone — it is a tool running on "
        "their machine. A `question` may be answered by anyone who may edit the card. "
        "Null for a session owned by nobody, which nobody can answer a permission for."
    )
    owner: str | None = Field(description="Whose agent is asking, by name.")
    status: AgentPromptStatus
    answer: str | None = Field(
        description="What was answered: the chosen option's label, or the reply typed."
    )
    answer_key: str | None = Field(description="The chosen option's key; null for a reply.")
    answered_by: str | None = Field(description="Who answered, by name.")
    answered_by_id: UUID | None = Field(
        description="Who answered, as a person id: what a viewer compares with their own "
        "to say 'You: …' rather than the name. Null when unanswered, or answered with a "
        "token that is nobody's."
    )
    answered_at: datetime | None
    closed_reason: str | None = Field(
        description="For `withdrawn`: `answered_in_terminal`, `interrupted`, `abandoned` "
        "or `session_ended`."
    )
    asked_at: datetime
    expires_at: datetime = Field(description="After this nobody may answer it.")
    closed_at: datetime | None


class AgentSessionRead(ReadModel):
    id: UUID
    task_id: UUID
    actor_label: str = Field(description="The name of the token the hook reports with.")
    client_name: str | None = Field(description="The session's display name, if known.")
    state: AgentSessionState
    reason: AgentSessionReason | None
    note: str | None = Field(description="What the session is doing right now, if it said.")
    started_at: datetime
    state_changed_at: datetime = Field(description="When `state` last changed.")
    last_seen_at: datetime = Field(
        description="When the session last reported in, over its socket or the HTTP route."
    )
    ended_at: datetime | None
    dismissed_at: datetime | None
    prompt: AgentPromptRead | None = Field(
        default=None,
        description="What the session is waiting on a person for, while it is; otherwise "
        "the last prompt it closed since entering its current state, so a card can say "
        "'You: Allow once'. Null when neither.",
    )


PresenceState = Literal["working", "waiting", "done"]
"""What a card's border says.

Three, not four. There was a `stale` — a working session gone quiet past a
threshold — for as long as nothing ended the rows of processes that died
without saying so. Something does now, so the board reads a fact instead of
a guess about the clock.
"""


class AgentPresence(ReadModel):
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
    note: str | None = Field(
        description="What the deciding session is doing right now — its current tool call."
    )
    since: datetime = Field(description="When the deciding session entered its state.")
    last_seen_at: datetime = Field(description="When the deciding session last reported in.")
    prompt: AgentPromptRead | None = Field(
        default=None,
        description="The deciding session's open question or permission, which a person "
        "can answer from the card — or, once answered, the answer, until the session "
        "next changes state.",
    )


class AgentFeedEntryRead(ReadModel):
    """One line of what an agent read, edited, ran or said on a card."""

    id: UUID
    agent_session_id: UUID
    client_name: str | None = Field(description="The session's display name, if known.")
    at: datetime
    kind: AgentFeedKind
    tool: str | None
    text: str
