"""Reading prompts back: what a card shows, and what a card was asked."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.agent_session import AgentPrompt, AgentPromptKind, AgentSession
from app.models.task import Task
from app.schemas.agent_sessions import AgentPromptOption, AgentPromptRead


async def for_sessions(
    session: AsyncSession, rows: Sequence[AgentSession]
) -> dict[UUID, AgentPrompt]:
    """The prompt each session shows: its open one, else the last it closed
    since entering its current state. One query for every session given.

    Open before closed and oldest open first, because two open at once — a
    question and a permission from parallel tool calls — are answered in the
    order they were asked.
    """
    if not rows:
        return {}
    by_id = {row.id: row for row in rows}
    found = await session.scalars(
        select(AgentPrompt)
        .where(AgentPrompt.agent_session_id.in_(by_id))
        .order_by(
            AgentPrompt.closed_at.is_not(None),
            AgentPrompt.closed_at.desc(),
            AgentPrompt.asked_at,
        )
    )
    shown: dict[UUID, AgentPrompt] = {}
    for prompt in found:
        if prompt.agent_session_id in shown:
            continue
        row = by_id[prompt.agent_session_id]
        if prompt.closed_at is None or prompt.closed_at >= row.state_changed_at:
            shown[prompt.agent_session_id] = prompt
    return shown


async def shown_for(
    session: AsyncSession, rows: Sequence[AgentSession]
) -> dict[UUID, AgentPromptRead]:
    """:func:`for_sessions`, read: what each of these sessions' cards shows."""
    shown = await for_sessions(session, rows)
    return {row_id: read(prompt) for row_id, prompt in shown.items()}


async def recent(session: AsyncSession, task: Task, *, limit: int) -> list[AgentPromptRead]:
    """A card's prompts, newest first — what was asked of whom, and what came of it."""
    found = await session.scalars(
        select(AgentPrompt)
        .where(AgentPrompt.task_id == task.id)
        .order_by(AgentPrompt.asked_at.desc())
        .limit(limit)
    )
    return [read(prompt) for prompt in found]


def read(prompt: AgentPrompt) -> AgentPromptRead:
    """A prompt as the API shows it."""
    return AgentPromptRead(
        id=prompt.id,
        agent_session_id=prompt.agent_session_id,
        task_id=prompt.task_id,
        kind=prompt.kind,
        text=prompt.text,
        tool=prompt.tool,
        options=options_of(prompt),
        allows_reply=prompt.kind is AgentPromptKind.QUESTION,
        status=prompt.status,
        answer=prompt.answer,
        answer_key=prompt.answer_key,
        answered_by=prompt.answered_by_label,
        answered_by_id=prompt.answered_by_id,
        answered_at=prompt.answered_at,
        closed_reason=prompt.closed_reason,
        asked_at=prompt.asked_at,
        expires_at=prompt.expires_at,
        closed_at=prompt.closed_at,
    )


def options_of(prompt: AgentPrompt) -> list[AgentPromptOption]:
    """The choices a prompt offers, read back out of the JSON they are stored as."""
    return [AgentPromptOption.model_validate(option) for option in prompt.options]
