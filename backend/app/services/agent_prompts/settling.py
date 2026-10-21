"""Closing prompts nobody closed on purpose: a session's report overtook them,
the session ended, or their time ran out."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import now as clock_now
from app.models.agent_session import (
    AgentPrompt,
    AgentPromptKind,
    AgentPromptStatus,
    AgentSession,
    AgentSessionReason,
    AgentSessionState,
    SessionKey,
)
from app.models.task import Task
from app.schemas.agent_sessions import AgentSessionPut
from app.services import agent_sessions
from app.services.agent_prompts import store

ABANDONED_AFTER = timedelta(seconds=90)
"""A question whose tool has not polled for this long has no tool behind it.

The tool polls every twenty seconds or so; four missed polls is a call that
was cancelled without managing to say so — an MCP server killed with its
terminal, a hosted request cut off mid-wait."""


async def settle_for_report(
    session: AsyncSession, task: Task, key: SessionKey, data: AgentSessionPut
) -> AgentSessionPut:
    """What a session's own report means for the prompts it has open.

    Two rules, one for each kind:

    * **A permission is over once the session reports anything else.** The
      harness's ``PermissionRequest`` hook is not ended when its dialog is
      answered in the terminal — Claude Code leaves it running — so the
      first sign the terminal won is the next thing the session says: a tool
      starting, a turn ending. Only another "waiting for permission" leaves
      it open, since that is the same dialog's own ``Notification``.
    * **A working report while a live question is open is the question's
      turn.** The ``ask_human`` tool is running, so the session *is* working
      in the harness's eyes — and a daemon that reconnects mid-question
      re-sends exactly that level. The card should go on saying it waits on
      a person until the person answers, so the report is read as a
      heartbeat of that waiting. A question whose tool has stopped polling is
      withdrawn instead, and the report goes through.

    Returns the report to apply. A ``done`` is left to
    :func:`withdraw_for_sessions`, which the caller runs on whatever ended.
    """
    if data.state is AgentSessionState.DONE:
        return data
    row = await agent_sessions.open_on_card(session, task, key)
    if row is None:
        return data

    asking_permission = (
        data.state is AgentSessionState.WAITING and data.reason is AgentSessionReason.PERMISSION
    )
    now = clock_now()
    held = False
    for prompt in await store.open_prompts(session, [row.id]):
        if prompt.kind is AgentPromptKind.PERMISSION:
            if asking_permission:
                continue
            await store.close(
                session,
                prompt,
                AgentPromptStatus.WITHDRAWN,
                now,
                reason=store.ANSWERED_IN_TERMINAL,
                resume=False,
            )
        elif data.state is not AgentSessionState.WORKING:
            continue
        elif now - prompt.last_polled_at > ABANDONED_AFTER:
            await store.close(
                session,
                prompt,
                AgentPromptStatus.WITHDRAWN,
                now,
                reason=store.ABANDONED,
                resume=False,
            )
        else:
            await store.expire_if_due(session, prompt, now)

        if prompt.status is AgentPromptStatus.OPEN:
            held = True
        else:
            store.announce(session, task, prompt, row)

    if not held:
        return data
    return AgentSessionPut(
        state=AgentSessionState.WAITING,
        reason=AgentSessionReason.QUESTION,
        client_name=data.client_name,
        note=row.note,
    )


async def withdraw_for_sessions(
    session: AsyncSession, rows: Iterable[AgentSession], reason: str
) -> int:
    """Withdraw every open prompt of sessions that have just ended.

    Nobody is left to hear an answer. Their waiters are woken, so an
    ``ask_human`` that is mid-poll returns at once saying so.
    """
    by_id = {row.id: row for row in rows}
    if not by_id:
        return 0
    now = clock_now()
    closed = 0
    for prompt in await store.open_prompts(session, list(by_id)):
        await store.close(
            session, prompt, AgentPromptStatus.WITHDRAWN, now, reason=reason, resume=False
        )
        task = await session.get(Task, prompt.task_id)
        if task is not None:
            store.announce(session, task, prompt, by_id[prompt.agent_session_id])
        closed += 1
    return closed


async def sweep(session: AsyncSession) -> int:
    """Close the prompts nothing else will: overdue, or a question nobody polls.

    Run by the reaper. Every other close happens because somebody did
    something; these are the ones where the something was nothing.
    """
    now = clock_now()
    # Locked, skipping any row a person or the agent is closing this moment:
    # that close is the one that happens, and the next sweep sees the result.
    await session.flush()
    found = await session.scalars(
        select(AgentPrompt)
        .where(AgentPrompt.status == AgentPromptStatus.OPEN)
        .with_for_update(skip_locked=True)
        .execution_options(populate_existing=True)
    )
    closed = 0
    for prompt in list(found):
        if prompt.expires_at <= now:
            await store.expire_if_due(session, prompt, now)
        elif (
            prompt.kind is AgentPromptKind.QUESTION
            and now - prompt.last_polled_at > ABANDONED_AFTER
        ):
            await store.close(
                session, prompt, AgentPromptStatus.WITHDRAWN, now, reason=store.ABANDONED
            )

        if prompt.status is not AgentPromptStatus.OPEN:
            task = await session.get(Task, prompt.task_id)
            row = await session.get(AgentSession, prompt.agent_session_id)
            if task is not None:
                store.announce(session, task, prompt, row)
            closed += 1
    return closed
