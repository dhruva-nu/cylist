"""Liveness and readiness.

Unauthenticated on purpose: a load balancer or ``docker healthcheck`` must be
able to reach it without credentials. It reveals nothing but whether the
process can talk to its database.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Request, Response, status
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import SessionDependency
from app.schemas.common import Schema

router = APIRouter(tags=["health"])


class Health(Schema):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]


class RequestCounts(Schema):
    since: datetime
    total: int
    status_2xx: int
    status_3xx: int
    status_4xx: int
    status_5xx: int


class RealtimeCounts(Schema):
    agent_sockets: int
    board_watchers: int
    projects_watched: int


@router.get("/health", response_model=Health, summary="Service health")
async def health(
    response: Response,
    session: AsyncSession = SessionDependency,
) -> Health:
    """Report whether the API can serve requests.

    Returns ``503`` when the database is unreachable so orchestrators stop
    routing traffic here.
    """
    try:
        await session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return Health(status="degraded", database="down")
    return Health(status="ok", database="up")


@router.get("/health/requests", response_model=RequestCounts, summary="Request counters")
async def request_counts(request: Request) -> RequestCounts:
    """Responses served since this process started, by status class.

    Unauthenticated, like ``/health``: it reveals traffic volume and nothing
    about its content. Counts reset on every restart — read alongside
    ``since``, not as a running total across deploys.
    """
    metrics = request.app.state.metrics
    return RequestCounts(
        since=metrics.since,
        total=metrics.total,
        status_2xx=metrics.status_2xx,
        status_3xx=metrics.status_3xx,
        status_4xx=metrics.status_4xx,
        status_5xx=metrics.status_5xx,
    )


@router.get("/health/realtime", response_model=RealtimeCounts, summary="Live connections")
async def realtime_counts(request: Request) -> RealtimeCounts:
    """How many sockets this process is holding.

    The answer to a question that is otherwise unanswerable after the fact:
    did the WebSocket upgrade actually survive the proxy in production, on a
    day nobody was watching? A board that has silently fallen back to polling
    looks exactly like a working one from the outside, and shows up here as
    watchers that never arrive.

    Unauthenticated for the reason the other two are, and safe to be: counts
    only. No project, no session, no token, nothing about content.
    """
    return RealtimeCounts(**request.app.state.hub.stats())
