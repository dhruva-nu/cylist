"""What an agent session is waiting on a person for, and the person's answer.

Two ways an agent ends up waiting on a person in the middle of a turn, and
one model for both — :class:`~app.models.agent_session.AgentPrompt`:

* A **question** (CYLIST-85). The agent calls the MCP tool ``ask_human``,
  which :func:`ask`\\ s here and then long-polls :func:`wait` until the prompt
  closes; the answer is the tool's result, so the agent carries on in the
  same turn.
* A **permission** (CYLIST-89). Claude Code's ``PermissionRequest`` hook asks
  the session's presence daemon, the daemon sends a ``prompt`` frame down the
  socket it already holds, and :func:`open_permission` puts it on the card.
  The answer goes back down the same socket as an ``answer`` frame. The
  terminal's own dialog is open at the same time, and the first answer wins:
  the terminal winning is how a permission is *withdrawn*.

Whichever it is, a person answers with :func:`answer`, the session goes back
to ``working``, the answer is kept on the prompt so a card can say "You:
Allow once", and the activity trail gets a line.

How the news travels is the part to read twice. Every close publishes an
``agent.prompt`` event through :func:`app.db.publish_into`, so it goes out
only once the transaction commits. :meth:`app.realtime.hub.Hub.publish` turns
it into three things: the board's doorbell, a wake-up for any long-poll
waiting on that prompt, and — for a permission — the ``answer`` frame on the
agent's socket. A wake-up that is missed costs nothing but latency: a waiter
re-reads the row when its wait ends, and the daemon re-sends an unanswered
``prompt`` after a reconnect, which this module answers with the outcome.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.principal import Principal
from app.core.clock import now as clock_now
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.db import publish_into
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
from app.schemas.agent_sessions import (
    NOTE_MAX,
    AgentPromptAnswer,
    AgentPromptOption,
    AgentPromptRead,
    AgentQuestionCreate,
    AgentSessionPut,
)
from app.services import activity

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

ABANDONED_AFTER = timedelta(seconds=90)
"""A question whose tool has not polled for this long has no tool behind it.

The tool polls every twenty seconds or so; four missed polls is a call that
was cancelled without managing to say so — an MCP server killed with its
terminal, a hosted request cut off mid-wait."""

POLL_WAIT_MAX = 25
"""Longest a single :func:`wait` may block, in seconds. Under the thirty the
MCP client allows a request, and under any proxy's idle timeout."""


# --- Asking -------------------------------------------------------------------


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

    changed = row.state is not AgentSessionState.WAITING
    row.state = AgentSessionState.WAITING
    row.reason = AgentSessionReason.QUESTION
    row.note = _clip(data.question)
    row.last_seen_at = now
    if changed:
        row.state_changed_at = now
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
    mine = select(ApiToken.id).where(
        ApiToken.person_id == principal.person_id
        if principal.person_id is not None
        else ApiToken.id == principal.token_id
    )
    query = (
        select(AgentSession)
        .where(
            AgentSession.task_id == task.id,
            AgentSession.ended_at.is_(None),
            AgentSession.token_id.in_(mine),
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
    existing = await session.get(AgentPrompt, prompt_id)
    if existing is not None:
        if existing.task_id != task.id or existing.kind is not AgentPromptKind.PERMISSION:
            return protocol.answer(existing.id, AgentPromptStatus.WITHDRAWN)
        await _expire_if_due(session, existing, clock_now())
        if existing.status is AgentPromptStatus.OPEN:
            existing.last_polled_at = clock_now()
            return None
        return protocol.answer(
            existing.id, existing.status, key=existing.answer_key, answer=existing.answer
        )

    row = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task.id,
            AgentSession.client_session_id == client_session_id,
            AgentSession.ended_at.is_(None),
        )
    )
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
    row.note = _clip(text)
    row.last_seen_at = now
    await session.flush()
    _announce(session, task, prompt, client_session_id)
    return None


# --- Answering ------------------------------------------------------------------


async def answer(
    session: AsyncSession,
    principal: Principal,
    task: Task,
    prompt_id: UUID,
    data: AgentPromptAnswer,
) -> AgentPromptRead:
    """A person's answer: close the prompt, free the agent, say so on the card."""
    prompt = await _prompt_on(session, task, prompt_id)
    now = clock_now()
    await _expire_if_due(session, prompt, now)
    if prompt.status is not AgentPromptStatus.OPEN:
        raise ConflictError(_already_closed(prompt))

    if data.key is not None:
        chosen = next((o for o in _options(prompt) if o.key == data.key), None)
        if chosen is None:
            keys = ", ".join(o.key for o in _options(prompt)) or "none — reply with `text`"
            raise ConflictError(f"{data.key!r} is not one of this prompt's options ({keys}).")
        prompt.answer, prompt.answer_key = chosen.label, chosen.key
    else:
        if prompt.kind is AgentPromptKind.PERMISSION:
            raise ConflictError("A permission is answered with one of its options, not a reply.")
        prompt.answer, prompt.answer_key = data.text, None

    prompt.status = AgentPromptStatus.ANSWERED
    prompt.answered_at = now
    prompt.closed_at = now
    prompt.answered_by_id = principal.person_id
    prompt.answered_by_label = principal.label

    row = await session.get(AgentSession, prompt.agent_session_id)
    if row is not None:
        await _resume(session, row, now, state=AgentSessionState.WORKING)
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
    _announce(session, task, prompt, row.client_session_id if row else "")
    return read(prompt)


async def withdraw(
    session: AsyncSession,
    task: Task,
    prompt_id: UUID,
    reason: str,
    *,
    principal: Principal | None = None,
) -> AgentPromptRead:
    """The agent stopped waiting before anybody here answered.

    From the ``ask_human`` tool when its call is cancelled, and from the
    daemon when the terminal's prompt won the race or the hook gave up. A
    prompt already closed is left as it is: whichever answer came first is
    the one that happened. ``expired`` is accepted as a reason and recorded
    as that status, for the daemon's own timeout.

    ``principal``, when given, must be an agent: a person does not withdraw an
    agent's question, they answer it.
    """
    if principal is not None and principal.channel is not Channel.API:
        raise ForbiddenError("Only the agent that is waiting can withdraw a prompt.")
    prompt = await _prompt_on(session, task, prompt_id)
    if prompt.status is AgentPromptStatus.OPEN:
        now = clock_now()
        status = AgentPromptStatus.EXPIRED if reason == "expired" else AgentPromptStatus.WITHDRAWN
        row = await _close(session, prompt, status, now, reason=reason)
        await session.flush()
        _announce(session, task, prompt, row.client_session_id if row else "")
        return read(prompt)
    return read(prompt)


async def wait_state(
    session: AsyncSession, task: Task, prompt_id: UUID, *, polled: bool
) -> AgentPromptRead:
    """A prompt as it stands, expiring it if it is due.

    ``polled`` is the asking tool's own look, which is what keeps a question
    from being taken for abandoned; anyone else reading it leaves that alone.
    """
    prompt = await _prompt_on(session, task, prompt_id)
    now = clock_now()
    if polled and prompt.status is AgentPromptStatus.OPEN:
        prompt.last_polled_at = now
    if await _expire_if_due(session, prompt, now) is not None:
        _announce(session, task, prompt, "")
    await session.flush()
    return read(prompt)


# --- What a session's reports mean for its prompts -------------------------------


async def settle_for_report(
    session: AsyncSession, task: Task, client_session_id: str, data: AgentSessionPut
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
    row = await session.scalar(
        select(AgentSession).where(
            AgentSession.task_id == task.id,
            AgentSession.client_session_id == client_session_id,
            AgentSession.ended_at.is_(None),
        )
    )
    if row is None:
        return data
    asking_permission = (
        data.state is AgentSessionState.WAITING and data.reason is AgentSessionReason.PERMISSION
    )
    now = clock_now()
    held = False
    for prompt in await _open_prompts(session, [row.id]):
        if prompt.kind is AgentPromptKind.PERMISSION:
            if asking_permission:
                continue
            await _close(
                session,
                prompt,
                AgentPromptStatus.WITHDRAWN,
                now,
                reason="answered_in_terminal",
                resume=False,
            )
        elif data.state is not AgentSessionState.WORKING:
            continue
        elif now - prompt.last_polled_at > ABANDONED_AFTER:
            await _close(
                session, prompt, AgentPromptStatus.WITHDRAWN, now, reason="abandoned", resume=False
            )
        else:
            await _expire_if_due(session, prompt, now)
        if prompt.status is AgentPromptStatus.OPEN:
            held = True
        else:
            _announce(session, task, prompt, client_session_id)
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
    for prompt in await _open_prompts(session, list(by_id)):
        await _close(session, prompt, AgentPromptStatus.WITHDRAWN, now, reason=reason, resume=False)
        task = await session.get(Task, prompt.task_id)
        if task is not None:
            client = by_id[prompt.agent_session_id].client_session_id
            _announce(session, task, prompt, client)
        closed += 1
    return closed


async def sweep(session: AsyncSession) -> int:
    """Close the prompts nothing else will: overdue, or a question nobody polls.

    Run by the reaper. Every other close happens because somebody did
    something; these are the ones where the something was nothing.
    """
    now = clock_now()
    found = await session.scalars(
        select(AgentPrompt).where(AgentPrompt.status == AgentPromptStatus.OPEN)
    )
    closed = 0
    for prompt in list(found):
        before = prompt.status
        if prompt.expires_at <= now:
            await _expire_if_due(session, prompt, now)
        elif (
            prompt.kind is AgentPromptKind.QUESTION
            and now - prompt.last_polled_at > ABANDONED_AFTER
        ):
            await _close(session, prompt, AgentPromptStatus.WITHDRAWN, now, reason="abandoned")
        if prompt.status is not before:
            task = await session.get(Task, prompt.task_id)
            row = await session.get(AgentSession, prompt.agent_session_id)
            if task is not None:
                _announce(session, task, prompt, row.client_session_id if row else "")
            closed += 1
    return closed


# --- Reading ---------------------------------------------------------------------


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
        options=_options(prompt),
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


async def shown_for(
    session: AsyncSession, rows: Sequence[AgentSession]
) -> dict[UUID, AgentPromptRead]:
    """:func:`for_sessions`, read: what each of these sessions' cards shows."""
    return {row_id: read(prompt) for row_id, prompt in (await for_sessions(session, rows)).items()}


# --- Pieces ----------------------------------------------------------------------


async def _prompt_on(session: AsyncSession, task: Task, prompt_id: UUID) -> AgentPrompt:
    prompt = await session.get(AgentPrompt, prompt_id)
    if prompt is None or prompt.task_id != task.id:
        raise NotFoundError(f"No such prompt on {task.reference}.")
    return prompt


async def _open_prompts(session: AsyncSession, row_ids: list[UUID]) -> list[AgentPrompt]:
    found = await session.scalars(
        select(AgentPrompt)
        .where(
            AgentPrompt.agent_session_id.in_(row_ids),
            AgentPrompt.status == AgentPromptStatus.OPEN,
        )
        .order_by(AgentPrompt.asked_at)
    )
    return list(found)


async def _expire_if_due(
    session: AsyncSession, prompt: AgentPrompt, now: datetime
) -> AgentSession | None:
    """Expire an open prompt whose time is up. Returns its session if it did."""
    if prompt.status is not AgentPromptStatus.OPEN or prompt.expires_at > now:
        return None
    row = await _close(session, prompt, AgentPromptStatus.EXPIRED, now, reason=None)
    if row is not None:
        task = await session.get(Task, prompt.task_id)
        if task is not None:
            await activity.record(
                session,
                _agent_principal(row),
                "agent_session.unanswered",
                entity_type="task",
                entity_id=task.id,
                project_id=task.project_id,
                payload={"kind": prompt.kind.value, "text": prompt.text},
            )
    return row


async def _close(
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
    rather than a guess: see :func:`_after_closing`. ``resume=False`` is for
    callers whose report is about to set the state anyway.
    """
    prompt.status = status
    prompt.closed_at = now
    prompt.closed_reason = reason if status is AgentPromptStatus.WITHDRAWN else None
    row = await session.get(AgentSession, prompt.agent_session_id)
    after = _after_closing(prompt.kind, status, reason)
    if row is not None and resume and after is not None:
        await _resume(session, row, now, state=after, except_id=prompt.id)
    return row


def _after_closing(
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
    if status is AgentPromptStatus.WITHDRAWN and reason == "answered_in_terminal":
        return AgentSessionState.WORKING
    return None


async def _resume(
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
        prompt
        for prompt in await _open_prompts(session, [row.id])
        if prompt.status is AgentPromptStatus.OPEN and prompt.id != except_id
    ]
    if still_open:
        return
    row.state = state
    row.reason = None if state is AgentSessionState.WORKING else AgentSessionReason.TURN_ENDED
    row.note = None
    row.state_changed_at = now
    row.last_seen_at = now


def _announce(
    session: AsyncSession, task: Task, prompt: AgentPrompt, client_session_id: str
) -> None:
    """Queue the ``agent.prompt`` event for a prompt that opened or closed.

    A closed permission carries its outcome for the agent's socket; nothing
    else carries anything but the doorbell and the wake-up.
    """
    deliver = (
        protocol.answer(prompt.id, prompt.status, key=prompt.answer_key, answer=prompt.answer)
        if prompt.kind is AgentPromptKind.PERMISSION and prompt.status is not AgentPromptStatus.OPEN
        else None
    )
    publish_into(session, _changed(task, prompt.id, client_session_id, deliver=deliver))


def _changed(
    task: Task, prompt_id: UUID, client_session_id: str, *, deliver: dict[str, object] | None
) -> dict[str, Any]:
    """The ``agent.prompt`` event, for :meth:`app.realtime.hub.Hub.publish`."""
    return {
        "type": protocol.PROMPT_CHANGED,
        "project_id": task.project_id,
        "task_id": task.id,
        "prompt_id": prompt_id,
        "client_session_id": client_session_id,
        "deliver": deliver,
    }


def _options(prompt: AgentPrompt) -> list[AgentPromptOption]:
    return [AgentPromptOption.model_validate(option) for option in prompt.options]


def _already_closed(prompt: AgentPrompt) -> str:
    if prompt.status is AgentPromptStatus.ANSWERED:
        who = f" by {prompt.answered_by_label}" if prompt.answered_by_label else ""
        return f"Already answered{who}: {prompt.answer}."
    if prompt.status is AgentPromptStatus.EXPIRED:
        return "Nobody answered in time, and the agent has stopped waiting."
    if prompt.closed_reason == "answered_in_terminal":
        return "It was answered in the terminal first."
    return "The agent has stopped waiting for an answer."


def _clip(text: str) -> str:
    line = " ".join(text.split())
    return line if len(line) <= NOTE_MAX else line[: NOTE_MAX - 1] + "…"


def _agent_principal(row: AgentSession) -> Principal:
    """The agent a prompt belonged to, for a trail line nobody in particular caused."""
    return Principal(
        token_id=row.token_id,
        person_id=None,
        label=row.actor_label,
        scopes=frozenset(),
        channel=Channel.API,
    )
