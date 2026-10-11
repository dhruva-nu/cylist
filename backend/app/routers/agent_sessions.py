"""Agent sessions: a harness hook reporting on a card, and a card read back.

Every path names a task the way every other task route does — by id or by
reference — because the hook only ever knows the reference the session was
bound with.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Path, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.db import SessionDependency
from app.models.task import Task
from app.routers.tasks import resolved_task
from app.schemas.agent_sessions import AgentFeedEntryRead, AgentSessionPut, AgentSessionRead
from app.services import agent_feed, agent_reports, agent_sessions

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
    response_model=list[AgentSessionRead],
    summary="List the agent sessions on a task",
)
async def list_agent_sessions(
    task: Task = Depends(resolved_task),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[AgentSessionRead]:
    """Every session still worth showing: the open ones first, then the
    finished ones nobody has dismissed. Dismissed sessions are left out."""
    return [agent_sessions.read(row) for row in await agent_sessions.list_for_task(session, task)]


@router.get(
    "/tasks/{task_ref}/agent-feed",
    response_model=list[AgentFeedEntryRead],
    summary="Read what the agents on a task have been reading, editing, running and saying",
)
async def list_agent_feed(
    task: Task = Depends(resolved_task),
    limit: int = Query(default=100, ge=1, le=agent_feed.KEEP_PER_SESSION),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> list[AgentFeedEntryRead]:
    """The card's agent feed, newest first, across every session that was on it.

    Written by each session's presence daemon, which follows the Claude Code
    transcript and sends the agent's text and tool calls — redacted on the
    agent's machine before they leave it. Each session keeps its newest 200
    lines. A board socket says `agent.feed` with this card's id when it grows.
    """
    return await agent_feed.recent(session, task, limit=limit)


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
