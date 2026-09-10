"""Application entry point.

Run with::

    uvicorn app.main:app --reload

The API is mounted under ``/api/v1``. Its OpenAPI document at
``/api/v1/openapi.json`` is the contract every client is built from — the
React app's TypeScript types, and later the CLI and MCP server.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import AGENT_QUIET_AFTER, REAP_EVERY, Settings, get_settings
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, describe_destination
from app.core.metrics import Metrics, MetricsMiddleware
from app.core.request_log import RequestLogMiddleware
from app.db import Database
from app.realtime.hub import Hub
from app.routers import api_router
from app.services import agent_reports
from app.spa import mount_spa

API_PREFIX = "/api/v1"

Lifespan = Callable[[FastAPI], AbstractAsyncContextManager[None]]

logger = logging.getLogger(__name__)

DESCRIPTION = """
Cylist keeps a project's **board**, **files**, **vault** and **people** in one
place — for you in the browser, and for your agents over the same HTTP API.

Authenticate with either an `Authorization: Bearer cyl_…` header or the
session cookie set by `POST /auth/login`. Tokens carry scopes; read each
endpoint's description to see which one it needs.
""".strip()


def build_lifespan(settings: Settings) -> Lifespan:
    """Create the startup/shutdown handler for a given configuration."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The first line in the file after a restart, and the one that answers
        # "which build, pointed at what?" — the question every investigation
        # of a deployment starts with, and the one a log without it cannot.
        logger.info(
            "Cylist starting",
            extra={
                "context": {
                    "environment": settings.environment,
                    "database": settings.safe_database_url,
                    "data_dir": settings.data_dir,
                    "log_level": settings.log_level,
                    "log_to": describe_destination(settings),
                }
            },
        )
        settings.blob_dir.mkdir(parents=True, exist_ok=True)
        app.state.database = Database(settings.database_url, echo=settings.database_echo)
        # Committed changes reach the boards watching them. The hub was built
        # in the factory; this is where it starts being listened to.
        app.state.database.publish_to(app.state.hub.publish)

        # One process, just started, holding no sockets: whatever the rows
        # say, nothing is running. Without this a card left mid-turn by a
        # restart pulses forever, since nothing computes staleness on read.
        async with app.state.database.session() as session:
            await agent_reports.end_open_sessions(session)

        reaper = asyncio.create_task(_reap_forever(app), name="cylist-reaper")
        try:
            yield
        finally:
            reaper.cancel()
            with suppress(asyncio.CancelledError):
                await reaper
            await app.state.database.dispose()
            # Logged after the pool is closed, so the line is a statement that
            # shutdown finished rather than that it was attempted.
            logger.info("Cylist stopped")

    return lifespan


async def _reap_forever(app: FastAPI) -> None:
    """End open rows that have gone quiet and hold no socket.

    The backstop for a client that reports over HTTP and then dies: a socket
    closing says so at once, a PUT that stops coming says nothing. A session
    holding a socket is witnessed however long it has been silent, so only the
    unwitnessed are considered — see
    :func:`app.services.agent_reports.reap_unwitnessed`.

    Never allowed to die of one bad pass: a sweep that raises is logged and
    the loop carries on, because the alternative is a task that quietly stops
    and a board that quietly stops being right.
    """
    while True:
        await asyncio.sleep(REAP_EVERY.total_seconds())
        try:
            async with app.state.database.session() as session:
                await agent_reports.reap_unwitnessed(
                    session,
                    app.state.hub.live_session_ids(),
                    quiet_after=AGENT_QUIET_AFTER,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("The agent-session reaper failed a pass")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application.

    Taking settings as an argument keeps the factory testable: a test can build
    an app pointed at a throwaway database without touching the environment.
    """
    settings = settings or get_settings()

    # Before anything else in the factory: whatever the rest of it has to say,
    # including a complaint about the configuration, should already be going
    # to the places this deployment asked for.
    configure_logging(settings)

    app = FastAPI(
        title="Cylist",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=build_lifespan(settings),
        openapi_url=f"{API_PREFIX}/openapi.json",
        docs_url=f"{API_PREFIX}/docs",
        redoc_url=None,
    )

    # Set eagerly, not in the lifespan: tests build an app without running the
    # lifespan, and every request needs to reach this configuration.
    app.state.settings = settings

    # Beside the metrics, and for the same reason — but the reason is sharper
    # here. The test suite builds apps without running the lifespan, so
    # anything created only there does not exist in a test; the hub has to be
    # reachable from a route whether or not startup ran. The background work
    # that uses it does live in the lifespan, where it belongs.
    app.state.hub = Hub()

    # Read by GET /health/metrics. Set on app.state, not module-level, so each
    # app built by the test suite starts its own counters.
    app.state.metrics = Metrics()
    app.add_middleware(MetricsMiddleware, metrics=app.state.metrics)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,  # required for the session cookie
            allow_methods=["*"],
            allow_headers=["*"],
        )

    # Added last, so it is the outermost middleware: it times the whole stack
    # including CORS, and it binds the request id before anything inside can
    # log without one.
    app.add_middleware(RequestLogMiddleware, api_prefix=API_PREFIX)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)

    # Last, because it claims "/": in the production image the built SPA is
    # served from this same process, and in development there is nothing to
    # serve and this does nothing.
    mount_spa(app, settings.web_dir, reserved=API_PREFIX)
    return app


app = create_app()
