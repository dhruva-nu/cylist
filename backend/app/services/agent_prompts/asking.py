"""Opening a prompt: an agent's question, or a permission its harness wants."""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now as clock_now
from app.core.errors import ConflictError, ForbiddenError
from app.models.activity import Channel
from app.models.agent_session import (
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionReason,
    AgentSessionState,
)
from app.models.api_token import ApiToken
from app.models.task import Task
from app.realtime import protocol
from app.schemas.agent_sessions import AgentPromptOption, AgentPromptRead, AgentQuestionCreate
from app.services import activity, agent_sessions
from app.services.agent_prompts import store
from app.services.agent_prompts.reading import read

PERMISSION_OPTIONS: tuple[AgentPromptOption, ...] = (
    AgentPromptOption(key="allow_once", label="Allow once"),
    AgentPromptOption(key="allow_session", label="Allow for this session"),
    AgentPromptOption(key="deny", label="Deny"),
)
"""The three answers to a permission, which the hook turns into Claude Code's
``PermissionRequest`` decision. The same three the terminal offers."""

PERMISSION_WAIT = timedelta(seconds=590)
"""How long a permission stays answerable here. Ten seconds inside the
hook's own 600-second timeout, so the board stops offering it before the
harness stops listening for it."""


async def ask(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    data: AgentQuestionCreate,
) -> AgentPromptRead:
    """Put an agent's question on its card, and hold its session waiting on it.

    Asked from the agent's own token, about the card it is bound to. Which
    session is asking is found rather than said, because an MCP server is
    not told the harness's session id: the caller's open session on this card
    heard from most recently — "the caller's" meaning any token of the same
    person, since the hosted MCP server and the hook are often handed
    different tokens by the same person.

    Raises:
        ForbiddenError: if the caller is not an API token.
        ConflictError: if none of the caller's sessions is open on the card.
    """
    if principal.channel is not Channel.API:
        raise ForbiddenError("Only an agent's API token can ask a question on a card.")

    row = await _asking_session(session, principal, task, data.client_session_id)
    now = clock_now()
    prompt = AgentPrompt(
        agent_session_id=row.id,
        task_id=task.id,
        kind=AgentPromptKind.QUESTION,
        text=data.question,
        tool=None,
        options=[
            AgentPromptOption(key=str(index), label=label).model_dump()
            for index, label in enumerate(data.options, start=1)
        ],
        status=AgentPromptStatus.OPEN,
        asked_at=now,
        expires_at=now + timedelta(seconds=data.timeout_seconds),
        last_polled_at=now,
    )
    session.add(prompt)

    if row.state is not AgentSessionState.WAITING:
        row.state_changed_at = now
    row.state = AgentSessionState.WAITING
    row.reason = AgentSessionReason.QUESTION
    row.note = store.clip(data.question)
    row.last_seen_at = now
    await session.flush()

    await activity.record(
        session,
        principal,
        "agent_session.waiting",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={
            "client_session_id": row.client_session_id,
            "client_name": row.client_name,
            "reason": AgentSessionReason.QUESTION.value,
            "question": data.question,
        },
    )
    return read(prompt)


async def _asking_session(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    client_session_id: str | None,
) -> AgentSession:
    """The caller's open session on this card, or a refusal saying why there is none."""
    callers_tokens = select(ApiToken.id).where(
        ApiToken.person_id == principal.person_id
        if principal.person_id is not None
        else ApiToken.id == principal.token_id
    )
    query = (
        select(AgentSession)
        .where(
            AgentSession.task_id == task.id,
            AgentSession.ended_at.is_(None),
            AgentSession.token_id.in_(callers_tokens),
        )
        .order_by(AgentSession.last_seen_at.desc())
        .limit(1)
    )
    if client_session_id is not None:
        query = query.where(AgentSession.client_session_id == client_session_id)
    row = await session.scalar(query)
    if row is None:
        raise ConflictError(
            f"No session of yours is open on {task.reference}, so nobody would see the "
            f"question there. Bind the session first (/work {task.reference}), or ask "
            "in the terminal."
        )
    return row


async def open_permission(
    session: AsyncSession,
    task: Task,
    client_session_id: str,
    prompt_id: UUID,
    text: str,
    tool: str | None,
) -> dict[str, object] | None:
    """Put a permission the harness wants on the card, from the agent's socket.

    Returns the frame to send back at once, or ``None`` while it is open.
    Idempotent by ``prompt_id``: a daemon re-sends an unanswered prompt after
    a reconnect, and the reply to a prompt already closed is its outcome —
    which is how an answer given while the link was down still arrives.

    No open session on the card means nobody can see it: the reply says it is
    withdrawn, and the hook leaves the terminal's own prompt to it.
    """
    existing = await store.locked(session, prompt_id)
    if existing is not None:
        return await _resent(session, task, client_session_id, existing)

    row = await agent_sessions.open_on_card(session, task, client_session_id)
    if row is None:
        return protocol.answer(prompt_id, AgentPromptStatus.WITHDRAWN)

    now = clock_now()
    prompt = AgentPrompt(
        id=prompt_id,
        agent_session_id=row.id,
        task_id=task.id,
        kind=AgentPromptKind.PERMISSION,
        text=text,
        tool=tool,
        options=[option.model_dump() for option in PERMISSION_OPTIONS],
        status=AgentPromptStatus.OPEN,
        asked_at=now,
        expires_at=now + PERMISSION_WAIT,
        last_polled_at=now,
    )
    session.add(prompt)
    # The daemon sends the level — waiting, for permission — just before
    # this frame, so the row normally says so already. Made certain here all
    # the same: the prompt is what the card is for, and a level is only ever
    # the daemon's latest belief.
    if row.state is not AgentSessionState.WAITING:
        row.state = AgentSessionState.WAITING
        row.state_changed_at = now
    row.reason = AgentSessionReason.PERMISSION
    row.note = store.clip(text)
    row.last_seen_at = now
    await session.flush()
    store.announce(session, task, prompt, client_session_id)
    return None


async def _resent(
    session: AsyncSession, task: Task, client_session_id: str, existing: AgentPrompt
) -> dict[str, object] | None:
    """The reply to a permission the daemon has sent before: its outcome, or
    ``None`` while it is still open."""
    asker = await session.get(AgentSession, existing.agent_session_id)
    is_this_sessions = (
        existing.task_id == task.id
        and existing.kind is AgentPromptKind.PERMISSION
        and asker is not None
        and asker.client_session_id == client_session_id
    )
    if not is_this_sessions:
        # Not this session's to re-send: it hears nothing of another's.
        return protocol.answer(existing.id, AgentPromptStatus.WITHDRAWN)

    await store.expire_if_due(session, existing, clock_now())
    if existing.status is AgentPromptStatus.OPEN:
        existing.last_polled_at = clock_now()
        return None
    return protocol.answer(
        existing.id, existing.status, key=existing.answer_key, answer=existing.answer
    )
