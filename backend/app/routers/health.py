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


class TableSize(Schema):
    table: str
    rows: int
    """Live rows, as PostgreSQL's statistics count them: an estimate that
    tracks every insert and delete, not a ``count(*)``."""
    total_bytes: int
    """The table with its indexes and TOAST, on disk."""


class StorageCounts(Schema):
    tables: list[TableSize]


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


@router.get("/health/storage", response_model=StorageCounts, summary="Table sizes")
async def storage_counts(session: AsyncSession = SessionDependency) -> StorageCounts:
    """How many rows each table holds, and how much disk it takes.

    For watching the tables that grow with use — ``activity``,
    ``agent_session`` and its feed — between retention passes, and for
    noticing when one starts growing faster than retention trims it
    (CYLIST-139).

    Read from the statistics PostgreSQL keeps anyway rather than counted, so
    it costs the same on a table of ten rows as on one of ten million: an
    unauthenticated endpoint must not be a way to make the database scan
    everything. The counts are estimates that follow every write within
    moments; exact enough to watch a trend, not to reconcile.

    Unauthenticated for the reason the others are, and safe to be: table
    names are the published schema's, and sizes say how much, never what.
    """
    rows = await session.execute(
        text(
            """
            SELECT relname, n_live_tup, pg_total_relation_size(relid)
            FROM pg_stat_user_tables
            WHERE schemaname = current_schema()
            ORDER BY relname
            """
        )
    )
    return StorageCounts(
        tables=[
            TableSize(table=name, rows=live, total_bytes=size) for name, live, size in rows.all()
        ]
    )
