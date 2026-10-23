"""Agent sessions: a harness hook reporting on a card, and a card read back.

Every path names a task the way every other task route does — by id or by
reference — because the hook only ever knows the reference the session was
bound with.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.card_deps import WRITE_THIS_CARD, resolved_task
from app.api.paging import PageQuery
from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.activity import Channel
from app.models.task import Task
from app.realtime.hub import Hub
from app.schemas.agent_sessions import (
    AgentFeedPage,
    AgentPromptAnswer,
    AgentPromptPage,
    AgentPromptRead,
    AgentPromptWithdraw,
    AgentQuestionCreate,
    AgentSessionPage,
    AgentSessionPut,
    AgentSessionRead,
)
from app.services import agent_feed, agent_gateway, agent_prompts, agent_reports, agent_sessions
from app.services.paging import PageRequest

router = APIRouter(tags=["agent sessions"])

ClientSessionId = Path(
    description="The harness's own id for the conversation — Claude Code's `session_id`.",
    max_length=200,
    min_length=1,
)


@router.put(
    "/tasks/{task_ref}/agent-sessions/{client_session_id}",
    response_model=AgentSessionRead,
    summary="Report what an agent session on this task is doing",
    responses={403: {"description": "Not an API token, or one without `write`."}},
)
async def put_agent_session(
    body: AgentSessionPut,
    task: Task = Depends(resolved_task),
    client_session_id: str = ClientSessionId,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> AgentSessionRead:
    """Say that a harness session is `working`, `waiting` or `done` on this card.

    Meant for lifecycle hooks, not for the agent: Claude Code's hooks call
    this on every prompt, tool call and stop, and the board draws the card's
    border from it. The same state sent again is a heartbeat and writes
    nothing to the activity trail; a change of state writes one line.

    A session is on one card at a time. Reporting `working` here while the
    same `client_session_id` is open on another card ends that row with
    `reason=moved`.

    API tokens only. A browser session is a person, and a person is not an
    agent whatever they type — that is a 403.
    """
    return await agent_reports.report(session, principal, task, client_session_id, body)


@router.get(
    "/tasks/{task_ref}/agent-sessions",
    response_model=AgentSessionPage,
    summary="List the agent sessions on a task",
)
async def list_agent_sessions(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> AgentSessionPage:
    """The sessions still worth showing, a page at a time: the open ones first,
    then the finished ones nobody has dismissed. Dismissed sessions are left
    out. Each carries the prompt it shows — see `AgentSessionRead.prompt`."""
    window = await agent_sessions.page_for_task(session, task, page)
    shown = await agent_prompts.shown_for(session, window.rows)
    return AgentSessionPage(
        items=[agent_sessions.read(row, shown.get(row.id)) for row in window.rows],
        next=window.next,
    )


@router.get(
    "/tasks/{task_ref}/agent-feed",
    response_model=AgentFeedPage,
    summary="Read what the agents on a task have been reading, editing, running and saying",
)
async def list_agent_feed(
    task: Task = Depends(resolved_task),
    page: PageRequest = PageQuery,
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> AgentFeedPage:
    """The card's agent feed, newest first, across every session that was on
    it, a page at a time.

    Written by each session's presence daemon, which follows the Claude Code
    transcript and sends the agent's text and tool calls — redacted on the
    agent's machine before they leave it. Each session keeps its newest 200
    lines. A board socket says `agent.feed` with this card's id when it grows.
    """
    items, after = await agent_feed.recent(session, task, page)
    return AgentFeedPage(items=items, next=after)


@router.post(
    "/tasks/{task_ref}/agent-sessions/dismiss",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
    summary="Clear the finished agent sessions off a task",
)
async def dismiss_agent_sessions(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> Response:
    """Take the green border off. Only sessions that have ended are cleared;
    an agent still working or waiting stays on the card. Editing the card in
    the web UI does the same thing without the button."""
    await agent_sessions.dismiss(session, task)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Prompts: what an agent is waiting on a person for -----------------------

PromptId = Path(description="The prompt's id, from the card's `agent_session.prompt`.")


@router.post(
    "/tasks/{task_ref}/agent-prompts",
    response_model=AgentPromptRead,
    status_code=status.HTTP_201_CREATED,
    summary="Ask the person an agent works for a question, on its card",
    responses={
        403: {"description": "Not an API token, or one without `write`."},
        409: {"description": "None of the caller's sessions is open on this card."},
    },
)
async def ask_question(
    body: AgentQuestionCreate,
    task: Task = Depends(resolved_task),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> AgentPromptRead:
    """What the MCP tool `ask_human` calls. The question and its options go
    on the card, and the caller's session there turns amber — waiting, with
    reason `question` — until somebody answers it or it expires.

    The caller then long-polls `GET …/agent-prompts/{id}?wait=20` until the
    status is no longer `open`. Bookkeeping about the caller rather than a
    change to the project, so it is fenced by scope alone, like the session
    report beside it."""
    return await agent_prompts.ask(session, principal, task, body)


@router.get(
    "/tasks/{task_ref}/agent-prompts",
    response_model=AgentPromptPage,
    summary="List what the agents on a task have asked, newest first",
)
async def list_agent_prompts(
    task: Task = Depends(resolved_task),
    page: PageRequest = PageQuery,
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> AgentPromptPage:
    """Questions and permissions alike, open or not, with what came of each,
    a page at a time."""
    items, after = await agent_prompts.recent(session, task, page)
    return AgentPromptPage(items=items, next=after)


@router.get(
    "/tasks/{task_ref}/agent-prompts/{prompt_id}",
    response_model=AgentPromptRead,
    summary="Read one prompt, waiting for it to close if asked to",
)
async def get_agent_prompt(
    request: Request,
    task: Task = Depends(resolved_task),
    prompt_id: UUID = PromptId,
    wait: int = Query(
        default=0,
        ge=0,
        le=agent_prompts.POLL_WAIT_MAX,
        description="Seconds to wait for it to stop being `open` before answering. "
        "The asking agent's own poll with `wait` also tells the server it is still "
        "waiting, and keeps its session from being taken for gone.",
    ),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> AgentPromptRead:
    """A long-poll: returns as soon as the prompt is answered, expires or is
    withdrawn, or after `wait` seconds with it still `open` — ask again.

    The database connection is let go while it waits, so a hundred agents
    waiting on their people hold no more of the pool than none do."""
    hub: Hub = request.app.state.hub
    return await agent_gateway.long_poll_prompt(
        session,
        hub,
        task,
        prompt_id,
        wait=wait,
        principal=principal,
        is_agent=principal.channel is Channel.API,
    )


@router.post(
    "/tasks/{task_ref}/agent-prompts/{prompt_id}/answer",
    response_model=AgentPromptRead,
    summary="Answer what an agent on this task is waiting on",
    responses={
        403: {"description": "A permission, and the caller is not the person it is for."},
        409: {"description": "Already answered, expired, withdrawn, or not an option."},
    },
)
async def answer_agent_prompt(
    body: AgentPromptAnswer,
    task: Task = Depends(resolved_task),
    prompt_id: UUID = PromptId,
    principal: Principal = Depends(WRITE_THIS_CARD),
    session: AsyncSession = SessionDependency,
) -> AgentPromptRead:
    """Pick one of the prompt's options by `key`, or — for a question — type
    a reply as `text`. The first answer wins: one already closed is a 409
    saying how. The agent's session goes back to `working`, the answer is
    kept so the card can say "You: <answer>", and the agent is told — a
    question's tool returns it, a permission's hook turns it into Claude
    Code's decision.

    Answering directs the work on the card, so it needs the role's `tasks`
    permission, like editing the card does. A `permission` asks more: it is
    a tool running on one person's machine, so only that person — the
    prompt's `owner` — may answer it, and anyone else is a 403 naming them."""
    return await agent_prompts.answer(session, principal, task, prompt_id, body)


@router.post(
    "/tasks/{task_ref}/agent-prompts/{prompt_id}/withdraw",
    response_model=AgentPromptRead,
    summary="Stop waiting on a prompt before anybody answers it",
    responses={
        403: {"description": "Not an API token, or one without `write`."},
        404: {"description": "No such prompt on the card, or it is not one of the caller's."},
    },
)
async def withdraw_agent_prompt(
    body: AgentPromptWithdraw,
    task: Task = Depends(resolved_task),
    prompt_id: UUID = PromptId,
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = SessionDependency,
) -> AgentPromptRead:
    """For the agent: its tool call was cancelled, or the terminal answered
    first. A prompt already closed is returned as it is. Only the agents of
    the person whose session asked may withdraw it; anyone else's prompt is
    not found."""
    return await agent_prompts.withdraw(session, task, prompt_id, body.reason, principal=principal)
