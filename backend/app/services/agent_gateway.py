"""What an agent can ask of the server, however it reaches it.

An agent speaks to the board over two lines — plain HTTP, which is what the
CLI, the MCP server and the lifecycle hooks use, and a websocket, which the
presence daemon holds open — and for a while each carried its own copy of the
glue between a request and the services: finding the card a message names and
checking the agent's owner may see it, committing before a long wait, deciding
what a missing card means. The copies drifted in small ways, which is what a
second copy is for.

The transports now do only what is theirs — authenticate, decode, turn a
refusal into a status or a close code — and call these. Every function here
takes the caller's session and raises the application's own errors; none knows
what an HTTP status or a websocket frame is.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from contextlib import AbstractContextManager
from typing import Protocol
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now
from app.models.agent_session import AgentPromptStatus, SessionKey
from app.models.task import Task
from app.services import agent_feed, agent_prompts, agent_reports, tasks
from app.services.commands.agent_sessions import AgentFeedEntryIn, AgentSessionPut
from app.services.readmodels.agent_sessions import AgentPromptRead, AgentSessionRead


class PromptWaiters(Protocol):
    """Whatever can say "this prompt changed" — the realtime hub, in a running app."""

    def prompt_waiter(self, prompt_id: UUID) -> AbstractContextManager[asyncio.Event]: ...


async def report_state(
    session: AsyncSession,
    principal: Principal,
    key: SessionKey,
    reference: str,
    report: AgentSessionPut,
) -> AgentSessionRead:
    """Record what an agent session says it is doing on the card it names.

    Raises:
        NotFoundError: if the card is missing or out of reach.
    """
    task = await tasks.resolve_reachable(session, principal, reference)
    return await agent_reports.report(session, principal, task, key.client_session_id, report)


async def open_permission(
    session: AsyncSession,
    principal: Principal,
    key: SessionKey,
    reference: str,
    prompt_id: UUID,
    text: str,
    tool: str | None,
) -> dict[str, object] | None:
    """Put a permission an agent is waiting on onto its card.

    Returns the frame the caller owes the agent at once, or ``None`` while the
    prompt is open.

    Raises:
        NotFoundError: if the card is missing or out of reach.
    """
    task = await tasks.resolve_reachable(session, principal, reference)
    return await agent_prompts.open_permission(session, task, key, prompt_id, text, tool)


async def withdraw_permission(
    session: AsyncSession,
    principal: Principal,
    key: SessionKey,
    reference: str,
    prompt_id: UUID,
    reason: str,
) -> None:
    """Take a permission off its card because its agent stopped waiting.

    Raises:
        NotFoundError: if the card is missing or out of reach, or the prompt is
            not one this session asked.
    """
    task = await tasks.resolve_reachable(session, principal, reference)
    await agent_prompts.withdraw(session, task, prompt_id, reason, key=key)


async def append_feed(
    session: AsyncSession,
    principal: Principal,
    key: SessionKey,
    reference: str,
    entries: Sequence[AgentFeedEntryIn],
) -> int:
    """Keep the lines an agent session sent about what it did on a card.

    Raises:
        NotFoundError: if the card is missing or out of reach.
    """
    task = await tasks.resolve_reachable(session, principal, reference)
    return await agent_feed.append(session, task, key, entries)


async def long_poll_prompt(
    session: AsyncSession,
    waiters: PromptWaiters,
    task: Task,
    prompt_id: UUID,
    *,
    wait: int,
    principal: Principal,
    is_agent: bool,
) -> AgentPromptRead:
    """A prompt as it stands, held open up to ``wait`` seconds for it to close.

    Returns at once if it is no longer open, or when it is answered, expires or
    is withdrawn, or after ``wait`` seconds with it still open — the caller
    asks again.

    The session is committed before the wait and used again after it, so the
    database connection is not held while the agent sits there: a hundred
    agents waiting on their people hold no more of the pool than none do.
    ``is_agent`` is whether the caller is an API token, whose poll doubles as
    the asking tool's own look (see :func:`agent_prompts.wait_state`).
    """
    poller = principal if wait > 0 and is_agent else None
    with waiters.prompt_waiter(prompt_id) as changed:
        current = await agent_prompts.wait_state(session, task, prompt_id, poller=poller)
        if wait == 0 or current.status is not AgentPromptStatus.OPEN:
            return current
        await session.commit()
        # Woken early to expire it, rather than late by a whole poll.
        until_expiry = (current.expires_at - now()).total_seconds()
        try:
            async with asyncio.timeout(max(0.0, min(float(wait), until_expiry + 0.5))):
                await changed.wait()
        except TimeoutError:
            pass
    return await agent_prompts.wait_state(session, task, prompt_id, poller=None)
