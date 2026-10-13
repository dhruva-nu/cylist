"""How a prompt row is read, closed and announced — the pieces every door shares.

Asking, answering and settling each end in the same few moves: read the row
locked, close it, put the session back where the harness says it now is, and
queue the event that tells the board and the agent. They live here so the
three doors cannot each grow their own idea of what closing a prompt means.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import NotFoundError
from app.db import publish_into
from app.models.agent_session import (
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionReason,
    AgentSessionState,
)
from app.models.task import Task
from app.realtime import protocol
from app.schemas.agent_sessions import NOTE_MAX
from app.services import activity, agent_sessions

ANSWERED_IN_TERMINAL = "answered_in_terminal"
"""A permission's ``closed_reason`` when the terminal's own dialog won."""

ABANDONED = "abandoned"
"""A question's ``closed_reason`` when its tool stopped polling for it."""

SESSION_ENDED = "session_ended"
"""A prompt's ``closed_reason`` when the session that asked it ended."""


async def prompt_on(session: AsyncSession, task: Task, prompt_id: UUID) -> AgentPrompt:
    """A prompt on this card, read locked, or a 404 that names the card.

    Raises:
        NotFoundError: if there is no such prompt, or it is on another card.
    """
    prompt = await locked(session, prompt_id)
    if prompt is None or prompt.task_id != task.id:
        raise NotFoundError(f"No such prompt on {task.reference}.")
    return prompt


async def locked(session: AsyncSession, prompt_id: UUID) -> AgentPrompt | None:
    """A prompt, read fresh and locked until this transaction ends.

    Every close is read, check it is open, write. Two of them at once — two
    people answering, or an answer and the terminal winning — would each see
    it open, and the second write would land on top of the first: the trail
    and the agent disagreeing about what was answered, or a withdrawal over an
    answer. Locked, the second waits, then reads the first one's result and is
    told it already closed. First answer wins, in the database too.

    Flushed first: re-reading overwrites what is loaded, and the session does
    not autoflush, so a change made earlier in this transaction would be lost.
    """
    await session.flush()
    return await session.get(AgentPrompt, prompt_id, with_for_update=True, populate_existing=True)


async def open_prompts(session: AsyncSession, row_ids: list[UUID]) -> list[AgentPrompt]:
    """The open prompts of these sessions, oldest first, flushed and locked as
    :func:`locked` explains."""
    await session.flush()
    found = await session.scalars(
        select(AgentPrompt)
        .where(
            AgentPrompt.agent_session_id.in_(row_ids),
            AgentPrompt.status == AgentPromptStatus.OPEN,
        )
        .order_by(AgentPrompt.asked_at)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    # Locking waits on a close in flight; what it closed is no longer open.
    return [prompt for prompt in found if prompt.status is AgentPromptStatus.OPEN]


async def expire_if_due(
    session: AsyncSession, prompt: AgentPrompt, now: datetime
) -> AgentSession | None:
    """Expire an open prompt whose time is up. Returns its session if it did."""
    if prompt.status is not AgentPromptStatus.OPEN or prompt.expires_at > now:
        return None
    row = await close(session, prompt, AgentPromptStatus.EXPIRED, now, reason=None)
    if row is not None:
        task = await session.get(Task, prompt.task_id)
        if task is not None:
            await activity.record(
                session,
                agent_sessions.acting_principal(row),
                "agent_session.unanswered",
                entity_type="task",
                entity_id=task.id,
                project_id=task.project_id,
                payload={"kind": prompt.kind.value, "text": prompt.text},
            )
    return row


async def close(
    session: AsyncSession,
    prompt: AgentPrompt,
    status: AgentPromptStatus,
    now: datetime,
    *,
    reason: str | None,
    resume: bool = True,
) -> AgentSession | None:
    """Close a prompt without an answer, and put its session where it now is.

    Where that is depends on what closed it, and is the harness's truth
    rather than a guess: see :func:`_state_after_closing`. ``resume=False`` is
    for callers whose report is about to set the state anyway.
    """
    prompt.status = status
    prompt.closed_at = now
    prompt.closed_reason = reason if status is AgentPromptStatus.WITHDRAWN else None
    row = await session.get(AgentSession, prompt.agent_session_id)
    after = _state_after_closing(prompt.kind, status, reason)
    if row is not None and resume and after is not None:
        await resume_session(session, row, now, state=after, except_id=prompt.id)
    return row


def _state_after_closing(
    kind: AgentPromptKind, status: AgentPromptStatus, reason: str | None
) -> AgentSessionState | None:
    """What a session is doing once a prompt closed without an answer here.

    * A question that expired: its tool returned saying so, and the agent
      carries on — working.
    * A question withdrawn (interrupted, abandoned): the turn was cut short,
      and the session waits on its human for the next prompt.
    * A permission answered in the terminal: the tool runs, or Claude is
      told no and carries on — working either way.
    * A permission that expired here: the terminal's dialog is still open,
      so it is still waiting for permission — unchanged (``None``).
    """
    if kind is AgentPromptKind.QUESTION:
        if status is AgentPromptStatus.EXPIRED:
            return AgentSessionState.WORKING
        return AgentSessionState.WAITING
    if status is AgentPromptStatus.WITHDRAWN and reason == ANSWERED_IN_TERMINAL:
        return AgentSessionState.WORKING
    return None


async def resume_session(
    session: AsyncSession,
    row: AgentSession,
    now: datetime,
    *,
    state: AgentSessionState,
    except_id: UUID | None = None,
) -> None:
    """Take an open session off a prompt it was waiting on, unless another is open.

    Only a session that is waiting *on a prompt* is moved: one that has since
    reported something else has said where it is, and that wins.
    """
    if row.ended_at is not None or row.state is not AgentSessionState.WAITING:
        return
    if row.reason not in {AgentSessionReason.QUESTION, AgentSessionReason.PERMISSION}:
        return
    still_open = [
        prompt for prompt in await open_prompts(session, [row.id]) if prompt.id != except_id
    ]
    if still_open:
        return
    row.state = state
    row.reason = None if state is AgentSessionState.WORKING else AgentSessionReason.TURN_ENDED
    row.note = None
    row.state_changed_at = now
    row.last_seen_at = now


def announce(
    session: AsyncSession, task: Task, prompt: AgentPrompt, client_session_id: str
) -> None:
    """Queue the ``agent.prompt`` event for a prompt that opened or closed.

    :meth:`app.realtime.hub.Hub.publish` turns it into the board's doorbell
    and a wake-up for any long-poll on the prompt. A closed permission also
    carries its outcome, for the agent's socket; nothing else carries anything
    but those two.
    """
    closed_permission = (
        prompt.kind is AgentPromptKind.PERMISSION and prompt.status is not AgentPromptStatus.OPEN
    )
    deliver = (
        protocol.answer(prompt.id, prompt.status, key=prompt.answer_key, answer=prompt.answer)
        if closed_permission
        else None
    )
    publish_into(
        session,
        {
            "type": protocol.PROMPT_CHANGED,
            "project_id": task.project_id,
            "task_id": task.id,
            "prompt_id": prompt.id,
            "client_session_id": client_session_id,
            "deliver": deliver,
        },
    )


def clip(text: str) -> str:
    """``text`` as one line short enough to be a session's note."""
    line = " ".join(text.split())
    return line if len(line) <= NOTE_MAX else line[: NOTE_MAX - 1] + "…"
