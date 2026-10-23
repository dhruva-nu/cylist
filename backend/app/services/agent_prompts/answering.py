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
    SessionKey,
)
from app.models.person import Person
from app.models.task import Task
from app.services import activity
from app.services.agent_prompts import store
from app.services.agent_prompts.reading import options_of, read_one
from app.services.commands.agent_sessions import AgentPromptAnswer
from app.services.readmodels.agent_sessions import AgentPromptRead

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

    **Who may answer** differs by kind, and the rule is written down in the
    project's docs ("What agents get from Cylist"):

    * A **question** is the agent asking the project something, and anyone
      who may edit the card may answer it — the route's own fence.
    * A **permission** is Claude Code asking whether a tool may run on one
      person's machine, so only that person may answer it (CYLIST-141): the
      owner of the session asking. A teammate approving ``git push`` on your
      laptop is the thing this exists to prevent, however much they may edit
      the card.

    And a permission is answered by a person signed in, or with a token
    minted with the ``permit`` scope for a person's own device (the phone
    app's). Agents hold API tokens, and the agent asking holds one on the same
    disk as the hook: one that could grant itself "Allow for this session"
    would be walking past the very prompt Claude Code put in front of a
    person. An agent's token is never minted with ``permit``.

    Raises:
        NotFoundError: if there is no such prompt on the card.
        ForbiddenError: if a permission is answered by anyone but its owner,
            or with an API token that lacks the ``permit`` scope.
        ConflictError: if the prompt has already closed, or the answer is not
            one it accepts.
    """
    prompt = await store.prompt_on(session, task, prompt_id)
    row = await session.get(AgentSession, prompt.agent_session_id)
    if prompt.kind is AgentPromptKind.PERMISSION:
        await _require_may_permit(session, principal, row)
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
    store.announce(session, task, prompt, row)
    return await read_one(session, prompt)


async def _require_may_permit(
    session: AsyncSession, principal: Principal, row: AgentSession | None
) -> None:
    """Refuse anyone who may not grant or refuse this tool call.

    The owner first, because it is the answer a person most needs: being
    told to use another credential when no credential of theirs would do is
    a runaround. The 403 names the owner, so whoever reads it knows who to
    go and ask.

    Raises:
        ForbiddenError: if the caller is not the session's owner, or is but
            holds an API token without ``permit``.
    """
    if row is None or not _owns(principal, row):
        owner_id = row.person_id if row is not None else None
        owner = await session.get(Person, owner_id) if owner_id is not None else None
        whose = f"{owner.name}'s" if owner is not None else "its owner's"
        raise ForbiddenError(
            f"This permission is for a tool on {whose} machine, and only "
            f"{owner.name if owner is not None else 'they'} can answer it."
        )
    if not (principal.channel is Channel.WEB or principal.has(Scope.PERMIT)):
        raise ForbiddenError(
            "A permission is answered by a person on the board or on their phone, "
            "not with an agent's API token."
        )


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
    key: SessionKey | None = None,
) -> AgentPromptRead:
    """The agent stopped waiting before anybody here answered.

    From the ``ask_human`` tool when its call is cancelled, and from the
    daemon when the terminal's prompt won the race or the hook gave up. A
    prompt already closed is left as it is: whichever answer came first is
    the one that happened. ``expired`` is accepted as a reason and recorded
    as that status, for the daemon's own timeout.

    ``principal``, when given — the HTTP route's caller — must be an agent of
    the person whose session asked: a person does not withdraw an agent's
    question, they answer it, and one person's agent does not take another's
    prompt off the board (CYLIST-120). Any token of the owner's will do, since
    the MCP server that asked and the hook are often handed different ones.

    ``key``, when given — the agent socket's own — must be the very session
    that asked: one session does not get to take another's prompt off the
    board, even a sibling of the same person's.

    Either way, a prompt that is not the caller's is not found, as far as the
    caller can tell.

    Raises:
        ForbiddenError: if ``principal`` is not an agent.
        NotFoundError: if there is no such prompt on the card, or it is not
            the caller's.
    """
    if principal is not None and principal.channel is not Channel.API:
        raise ForbiddenError("Only the agent that is waiting can withdraw a prompt.")
    prompt = await store.prompt_on(session, task, prompt_id)
    asker = await session.get(AgentSession, prompt.agent_session_id)
    if principal is not None and (asker is None or not _owns(principal, asker)):
        raise NotFoundError(f"No such prompt on {task.reference}.")
    if key is not None and (asker is None or asker.key != key):
        raise NotFoundError(f"No such prompt on {task.reference}.")

    if prompt.status is AgentPromptStatus.OPEN:
        status = (
            AgentPromptStatus.EXPIRED
            if reason == AgentPromptStatus.EXPIRED.value
            else AgentPromptStatus.WITHDRAWN
        )
        row = await store.close(session, prompt, status, clock_now(), reason=reason)
        await session.flush()
        store.announce(session, task, prompt, row)
    return await read_one(session, prompt)


async def wait_state(
    session: AsyncSession, task: Task, prompt_id: UUID, *, poller: Principal | None
) -> AgentPromptRead:
    """A prompt as it stands, expiring it if it is due.

    ``poller`` is the caller when it is long-polling with an API token. If it
    is the asking session's owner, that is the asking tool's own look: it
    keeps the question from being taken for abandoned, and it keeps the
    session itself from being taken for gone (CYLIST-134). An ``ask_human``
    that holds its turn open for half an hour is the agent's whole life in
    that half hour, and a session that reports over HTTP says nothing else in
    it — so without this the reaper ended it at ten minutes and withdrew the
    question a person was about to answer. Anyone else reading the prompt,
    and anyone else's agent polling it, leaves both alone.
    """
    prompt = await store.prompt_on(session, task, prompt_id)
    now = clock_now()
    row = await session.get(AgentSession, prompt.agent_session_id)
    owns = poller is not None and row is not None and _owns(poller, row)
    if owns and prompt.status is AgentPromptStatus.OPEN:
        prompt.last_polled_at = now
        if row is not None and row.ended_at is None:
            row.last_seen_at = now
    if await store.expire_if_due(session, prompt, now) is not None:
        store.announce(session, task, prompt, row)
    await session.flush()
    return await read_one(session, prompt)


def _owns(principal: Principal, row: AgentSession) -> bool:
    """Whether this caller is the person whose session this is. Nobody owns a
    row without an owner — not even a caller who is nobody either."""
    return row.person_id is not None and row.person_id == principal.person_id


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
