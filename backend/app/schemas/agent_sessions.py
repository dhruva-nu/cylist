"""Agent sessions: what a hook sends, and what a card says back about them."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from app.schemas.common import Page, Schema
from app.services.commands.agent_sessions import (
    FEED_TEXT_MAX as FEED_TEXT_MAX,
)
from app.services.commands.agent_sessions import (
    NOTE_MAX as NOTE_MAX,
)
from app.services.commands.agent_sessions import (
    QUESTION_MAX as QUESTION_MAX,
)
from app.services.commands.agent_sessions import (
    QUESTION_TIMEOUT_DEFAULT as QUESTION_TIMEOUT_DEFAULT,
)
from app.services.commands.agent_sessions import (
    QUESTION_TIMEOUT_MAX as QUESTION_TIMEOUT_MAX,
)
from app.services.commands.agent_sessions import (
    REPLY_MAX as REPLY_MAX,
)
from app.services.commands.agent_sessions import (
    AgentFeedEntryIn as AgentFeedEntryIn,
)
from app.services.commands.agent_sessions import (
    AgentPromptAnswer as AgentPromptAnswer,
)
from app.services.commands.agent_sessions import (
    AgentPromptOption as AgentPromptOption,
)
from app.services.commands.agent_sessions import (
    AgentQuestionCreate as AgentQuestionCreate,
)
from app.services.commands.agent_sessions import (
    AgentSessionPut as AgentSessionPut,
)
from app.services.readmodels.agent_sessions import (
    AgentFeedEntryRead as AgentFeedEntryRead,
)
from app.services.readmodels.agent_sessions import (
    AgentPresence as AgentPresence,
)
from app.services.readmodels.agent_sessions import (
    AgentPromptRead as AgentPromptRead,
)
from app.services.readmodels.agent_sessions import (
    AgentSessionRead as AgentSessionRead,
)
from app.services.readmodels.agent_sessions import (
    PresenceState as PresenceState,
)

WithdrawReason = Literal["interrupted", "answered_in_terminal"]


class AgentPromptWithdraw(Schema):
    """The agent has stopped waiting, before anybody here answered."""

    reason: WithdrawReason = Field(
        default="interrupted",
        description="`interrupted`: the tool call was cancelled. `answered_in_terminal`: "
        "the harness's own prompt was answered first.",
    )


class AgentSessionPage(Page[AgentSessionRead]):
    """A page of a card's agent sessions: the open ones first."""


class AgentFeedPage(Page[AgentFeedEntryRead]):
    """A page of a card's agent feed, newest first."""


class AgentPromptPage(Page[AgentPromptRead]):
    """A page of what the agents on a card have asked, newest first."""
