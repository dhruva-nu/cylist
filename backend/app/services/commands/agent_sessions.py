"""The commands the agent sessions services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.agent_sessions``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import Field, field_validator, model_validator

from app.models.agent_session import AgentFeedKind, AgentSessionReason, AgentSessionState
from app.services.commands.base import Command

NOTE_MAX = 300
"""Longest note a report may carry. A line on a card, not a log."""


class AgentSessionPut(Command):
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


class AgentPromptOption(Command):
    """One choice a prompt offers."""

    key: str = Field(max_length=40, description="What an answer names to pick this.")
    label: str = Field(max_length=200, description="What the button says.")


QUESTION_MAX = 1000
"""Longest question an agent may ask. A question for a person, not a document."""


QUESTION_TIMEOUT_DEFAULT = 1800


QUESTION_TIMEOUT_MAX = 4 * 3600


class AgentQuestionCreate(Command):
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


class AgentPromptAnswer(Command):
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


FEED_TEXT_MAX = 2000
"""Longest line the feed keeps. The CLI clips well below this; the ceiling is
here so that a client which does not cannot fill a row with a whole file."""


class AgentFeedEntryIn(Command):
    """One line of a session's feed, as its daemon sends it."""

    at: datetime = Field(description="When the agent did it, by the transcript's clock.")
    kind: AgentFeedKind
    tool: str | None = Field(
        default=None, max_length=200, description="The tool's own name, for every kind but `said`."
    )
    text: str = Field(min_length=1, max_length=FEED_TEXT_MAX)
