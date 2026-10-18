"""Closing one prompt on purpose: a person answers it, or the agent stops waiting."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.core.clock import now as clock_now
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.models.activity import Channel
from app.models.agent_session import (
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionState,
)
from app.models.task import Task
from app.schemas.agent_sessions import AgentPromptAnswer, AgentPromptRead
from app.services import activity
from app.services.agent_prompts import store
from app.services.agent_prompts.reading import options_of, read

POLL_WAIT_MAX = 25
"""Longest a single long-poll on a prompt may block, in seconds. Under the
thirty the MCP client allows a request, and under any proxy's idle timeout."""


async def answer(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    prompt_id: UUID,
    data: AgentPromptAnswer,
) -> AgentPromptRead:
    """A person's answer: close the prompt, free the agent, say so on the card.

    A permission is answered by a person signed in, or with a token minted
    with the ``permit`` scope for a person's own device (the phone app's).
    Agents hold API tokens, and the agent asking holds one on the same disk as
    the hook: one that could grant itself "Allow for this session" would be
    walking past the very prompt Claude Code put in front of a person. An
    agent's token is never minted with ``permit``.

    Raises:
        NotFoundError: if there is no such prompt on the card.
        ForbiddenError: if a permission is answered with an API token that
            lacks the ``permit`` scope.
        ConflictError: if the prompt has already closed, or the answer is not
            one it accepts.
    """
    prompt = await store.prompt_on(session, task, prompt_id)
    if prompt.kind is AgentPromptKind.PERMISSION and not _may_permit(principal):
        raise ForbiddenError(
            "A permission is answered by a person on the board or on their phone, "
            "not with an agent's API token."
        )
    now = clock_now()
    await store.expire_if_due(session, prompt, now)
    if prompt.status is not AgentPromptStatus.OPEN:
        raise ConflictError(_already_closed(prompt))

    _record_answer(prompt, data)
    prompt.status = AgentPromptStatus.ANSWERED
    prompt.answered_at = now
    prompt.closed_at = now
    prompt.answered_by_id = principal.person_id
    prompt.answered_by_label = principal.label

    row = await session.get(AgentSession, prompt.agent_session_id)
    if row is not None:
        await store.resume_session(session, row, now, state=AgentSessionState.WORKING)
    await session.flush()

    await activity.record(
        session,
        principal,
        "agent_session.answered",
        entity_type="task",
        entity_id=task.id,
        project_id=task.project_id,
        payload={
            "kind": prompt.kind.value,
            "text": prompt.text,
            "answer": prompt.answer,
            "answer_key": prompt.answer_key,
            "client_name": row.client_name if row else None,
        },
    )
    store.announce(session, task, prompt, row.client_session_id if row else "")
    return read(prompt)


def _may_permit(principal: Principal) -> bool:
    """Whether this credential may grant or refuse a tool call: a browser
    session, or a token carrying ``permit``."""
    return principal.channel is Channel.WEB or principal.has(Scope.PERMIT)


def _record_answer(prompt: AgentPrompt, data: AgentPromptAnswer) -> None:
    """Write what was answered onto the prompt: a chosen option, or a reply.

    Raises:
        ConflictError: if the key names none of the prompt's options, or a
            permission is answered with a typed reply.
    """
    if data.key is not None:
        offered = options_of(prompt)
        chosen = next((option for option in offered if option.key == data.key), None)
        if chosen is None:
            keys = ", ".join(option.key for option in offered) or "none — reply with `text`"
            raise ConflictError(f"{data.key!r} is not one of this prompt's options ({keys}).")
        prompt.answer, prompt.answer_key = chosen.label, chosen.key
        return

    if prompt.kind is AgentPromptKind.PERMISSION:
        raise ConflictError("A permission is answered with one of its options, not a reply.")
    prompt.answer, prompt.answer_key = data.text, None


async def withdraw(
    session: AsyncSession,
    task: Task,
    prompt_id: UUID,
    reason: str,
    *,
    principal: Principal | None = None,
    client_session_id: str | None = None,
) -> AgentPromptRead:
    """The agent stopped waiting before anybody here answered.

    From the ``ask_human`` tool when its call is cancelled, and from the
    daemon when the terminal's prompt won the race or the hook gave up. A
    prompt already closed is left as it is: whichever answer came first is
    the one that happened. ``expired`` is accepted as a reason and recorded
    as that status, for the daemon's own timeout.

    ``principal``, when given, must be an agent: a person does not withdraw an
    agent's question, they answer it.

    ``client_session_id``, when given — the agent socket's own — must be the
    session that asked: one session does not get to take another's prompt
    off the board. Any other is not found, as far as that socket can tell.

    Raises:
        ForbiddenError: if ``principal`` is not an agent.
        NotFoundError: if there is no such prompt on the card, or it is not
            ``client_session_id``'s.
    """
    if principal is not None and principal.channel is not Channel.API:
        raise ForbiddenError("Only the agent that is waiting can withdraw a prompt.")
    prompt = await store.prompt_on(session, task, prompt_id)
    if client_session_id is not None:
        asker = await session.get(AgentSession, prompt.agent_session_id)
        if asker is None or asker.client_session_id != client_session_id:
            raise NotFoundError(f"No such prompt on {task.reference}.")

    if prompt.status is AgentPromptStatus.OPEN:
        status = (
            AgentPromptStatus.EXPIRED
            if reason == AgentPromptStatus.EXPIRED.value
            else AgentPromptStatus.WITHDRAWN
        )
        row = await store.close(session, prompt, status, clock_now(), reason=reason)
        await session.flush()
        store.announce(session, task, prompt, row.client_session_id if row else "")
    return read(prompt)


async def wait_state(
    session: AsyncSession, task: Task, prompt_id: UUID, *, polled: bool
) -> AgentPromptRead:
    """A prompt as it stands, expiring it if it is due.

    ``polled`` is the asking tool's own look, which is what keeps a question
    from being taken for abandoned; anyone else reading it leaves that alone.
    """
    prompt = await store.prompt_on(session, task, prompt_id)
    now = clock_now()
    if polled and prompt.status is AgentPromptStatus.OPEN:
        prompt.last_polled_at = now
    if await store.expire_if_due(session, prompt, now) is not None:
        store.announce(session, task, prompt, "")
    await session.flush()
    return read(prompt)


def _already_closed(prompt: AgentPrompt) -> str:
    """Why an answer came too late, in the words a person reads on the card."""
    if prompt.status is AgentPromptStatus.ANSWERED:
        who = f" by {prompt.answered_by_label}" if prompt.answered_by_label else ""
        return f"Already answered{who}: {prompt.answer}."
    if prompt.status is AgentPromptStatus.EXPIRED:
        return "Nobody answered in time, and the agent has stopped waiting."
    if prompt.closed_reason == store.ANSWERED_IN_TERMINAL:
        return "It was answered in the terminal first."
    return "The agent has stopped waiting for an answer."
