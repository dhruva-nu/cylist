"""Agent sessions: what a hook sends, and what a card says back about them."""

from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from app.models.agent_session import (
    AgentFeedKind,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSessionReason,
    AgentSessionState,
)
from app.schemas.common import Schema

NOTE_MAX = 300
"""Longest note a report may carry. A line on a card, not a log."""


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
    note: str | None = Field(
        default=None,
        max_length=NOTE_MAX,
        description=(
            "One line on what the session is doing right now — 'Running pytest "
            "test_pr_states.py', 'Editing tasks.py' — or what it is waiting on. "
            "Every report replaces it, so a report without one clears it. Never "
            "written to the activity trail: it changes on every tool call."
        ),
    )

    @field_validator("client_name", "note")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class AgentPromptOption(Schema):
    """One choice a prompt offers."""

    key: str = Field(max_length=40, description="What an answer names to pick this.")
    label: str = Field(max_length=200, description="What the button says.")


class AgentPromptRead(Schema):
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
    status: AgentPromptStatus
    answer: str | None = Field(
        description="What was answered: the chosen option's label, or the reply typed."
    )
    answer_key: str | None = Field(description="The chosen option's key; null for a reply.")
    answered_by: str | None = Field(description="Who answered, by name.")
    answered_at: datetime | None
    closed_reason: str | None = Field(
        description="For `withdrawn`: `answered_in_terminal`, `interrupted`, `abandoned` "
        "or `session_ended`."
    )
    asked_at: datetime
    expires_at: datetime = Field(description="After this nobody may answer it.")
    closed_at: datetime | None


QUESTION_MAX = 1000
"""Longest question an agent may ask. A question for a person, not a document."""

QUESTION_TIMEOUT_DEFAULT = 1800
QUESTION_TIMEOUT_MAX = 4 * 3600


class AgentQuestionCreate(Schema):
    """An agent asking the person it works for something, and holding its turn."""

    question: str = Field(min_length=1, max_length=QUESTION_MAX)
    options: list[str] = Field(
        default_factory=list,
        max_length=6,
        description="Up to six choices, in order. A person may also type a reply.",
    )
    timeout_seconds: int = Field(
        default=QUESTION_TIMEOUT_DEFAULT,
        ge=30,
        le=QUESTION_TIMEOUT_MAX,
        description="How long to wait for an answer before the question expires.",
    )
    client_session_id: str | None = Field(
        default=None,
        max_length=200,
        description="Which of the caller's sessions on this card is asking. Omitted, the "
        "caller's session on the card heard from most recently.",
    )

    @field_validator("question")
    @classmethod
    def _strip_question(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("A question has to say something.")
        return stripped

    @field_validator("options")
    @classmethod
    def _clean_options(cls, value: list[str]) -> list[str]:
        cleaned = [option.strip() for option in value]
        if any(not option or len(option) > 200 for option in cleaned):
            raise ValueError("Each option is between 1 and 200 characters.")
        if len(set(cleaned)) != len(cleaned):
            raise ValueError("The options must differ from each other.")
        return cleaned


REPLY_MAX = 2000


class AgentPromptAnswer(Schema):
    """A person's answer: one of the options by key, or — to a question — a reply."""

    key: str | None = Field(default=None, max_length=40, description="The option picked.")
    text: str | None = Field(
        default=None,
        max_length=REPLY_MAX,
        description="A typed reply, for a question. Not accepted for a permission.",
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> AgentPromptAnswer:
        if self.text is not None:
            self.text = self.text.strip() or None
        if (self.key is None) == (self.text is None):
            raise ValueError("Answer with exactly one of `key` or `text`.")
        return self


WithdrawReason = Literal["interrupted", "answered_in_terminal"]


class AgentPromptWithdraw(Schema):
    """The agent has stopped waiting, before anybody here answered."""

    reason: WithdrawReason = Field(
        default="interrupted",
        description="`interrupted`: the tool call was cancelled. `answered_in_terminal`: "
        "the harness's own prompt was answered first.",
    )


class AgentSessionRead(Schema):
    id: UUID
    task_id: UUID
    actor_label: str = Field(description="The name of the token the hook reports with.")
    client_session_id: str = Field(description="The harness's own id for the conversation.")
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


FEED_TEXT_MAX = 2000
"""Longest line the feed keeps. The CLI clips well below this; the ceiling is
here so that a client which does not cannot fill a row with a whole file."""


class AgentFeedEntryIn(Schema):
    """One line of a session's feed, as its daemon sends it."""

    at: datetime = Field(description="When the agent did it, by the transcript's clock.")
    kind: AgentFeedKind
    tool: str | None = Field(
        default=None, max_length=200, description="The tool's own name, for every kind but `said`."
    )
    text: str = Field(min_length=1, max_length=FEED_TEXT_MAX)


class AgentFeedEntryRead(Schema):
    """One line of what an agent read, edited, ran or said on a card."""

    id: UUID
    agent_session_id: UUID
    client_name: str | None = Field(description="The session's display name, if known.")
    at: datetime
    kind: AgentFeedKind
    tool: str | None
    text: str
