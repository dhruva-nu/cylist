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
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.auth.throttle import SignInThrottle
from app.config import (
    AGENT_QUIET_AFTER,
    AGENT_SOCKET_WITNESSES_FOR,
    REAP_EVERY,
    Settings,
    get_settings,
)
from app.core.base_path import BasePathMiddleware
from app.core.errors import register_exception_handlers
from app.core.logging import configure_logging, describe_destination
from app.core.metrics import Metrics, MetricsMiddleware
from app.core.request_log import RequestLogMiddleware
from app.db import Database
from app.mcp import mount_mcp
from app.realtime.bus import Bus
from app.realtime.hub import Hub
from app.routers import api_router
from app.services import agent_prompts, agent_reports, hooks
from app.services.doc_engine import engine_for
from app.services.hook_delivery import Courier
from app.services.push import triggers as push_triggers
from app.services.push.delivery import Pusher
from app.services.push.fcm import PushSender, sender_for
from app.services.retention import Retention
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
        # Committed changes reach the boards watching them, and queued hook
        # deliveries wake the courier. Both were built in the factory; this is
        # where they start being listened to.
        app.state.database.publish_to(_publisher(app))
        # One sender, and so one HTTP client to FCM, for the process's life;
        # closed below, once the pusher that uses it has stopped.
        push_sender: PushSender = app.state.push_sender or sender_for(
            settings.fcm_service_account_file
        )
        pusher = asyncio.create_task(
            app.state.pusher.run(app.state.database, push_sender), name="cylist-pusher"
        )
        # The line to every other process of this deployment, if there are
        # any; harmless if there are not.
        bus = asyncio.create_task(app.state.bus.run(settings.database_url), name="cylist-bus")

        # No sweep of open sessions here, though there was one. A process that
        # has just started holds no sockets, but another process may, and a
        # daemon whose socket went down with the last one is reconnecting as
        # this runs. Sessions nobody reconnects to are the reaper's.
        reaper = asyncio.create_task(_reap_forever(app), name="cylist-reaper")
        courier = asyncio.create_task(
            app.state.courier.run(
                app.state.database, settings.vault_keyring, app.state.hook_transport
            ),
            name="cylist-hook-courier",
        )
        try:
            # The MCP tools at /mcp answer nothing until their session
            # managers are running, and those live for exactly this long.
            async with app.state.mcp.run():
                yield
        finally:
            for task in (reaper, courier, pusher, bus):
                task.cancel()
                with suppress(asyncio.CancelledError):
                    await task
            await push_sender.aclose()
            await app.state.database.dispose()
            # Logged after the pool is closed, so the line is a statement that
            # shutdown finished rather than that it was attempted.
            logger.info("Cylist stopped")

    return lifespan


def _publisher(app: FastAPI) -> Callable[[dict[str, Any]], None]:
    """Where a committed transaction's news goes: hook deliveries to the
    courier, agent-session pushes to the pusher, everything else to the
    boards — this process's and every other's.

    The courier and the pusher are told only here, in the process that
    committed: a hook delivery is a row any process's courier may claim, and
    a push is sent once, by whoever made it news."""
    bus: Bus = app.state.bus
    courier: Courier = app.state.courier
    pusher: Pusher = app.state.pusher

    def publish(event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == hooks.QUEUED:
            courier.wake()
        elif kind == push_triggers.QUEUED:
            pusher.notify(event)
        else:
            bus.publish(event)

    return publish


async def _reap_forever(app: FastAPI) -> None:
    """End open rows that have gone quiet and hold no socket.

    Also closes the prompts nothing else will — see
    :func:`app.services.agent_prompts.sweep`.

    The backstop for a client that reports over HTTP and then dies, and for a
    session whose socket went down with its process: a socket closing says so
    at once, a PUT that stops coming says nothing. A session a socket has
    vouched for lately is witnessed however long it has been silent, so only
    the unwitnessed are considered — see
    :func:`app.services.agent_reports.reap_unwitnessed`.

    Every process runs one, and one pass at a time does the work: the pass
    that takes the lease reaps, and the rest find it held and wait for the
    next — see :data:`app.services.agent_reports.REAPER_LEASE`.

    Never allowed to die of one bad pass: a sweep that raises is logged and
    the loop carries on, because the alternative is a task that quietly stops
    and a board that quietly stops being right.
    """
    while True:
        await asyncio.sleep(REAP_EVERY.total_seconds())
        try:
            async with app.state.database.session() as session:
                if not await agent_reports.take_reaper_lease(session):
                    continue
                await agent_reports.reap_unwitnessed(
                    session,
                    quiet_after=AGENT_QUIET_AFTER,
                    witnessed_for=AGENT_SOCKET_WITNESSES_FOR,
                )
                # Questions and permissions whose time ran out with nobody
                # looking, and questions whose tool is no longer polling.
                await agent_prompts.sweep(session)
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
        version="0.4.2",
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
    # The hub's reach beyond this process. Built here for the hub's reason;
    # its line to the others opens in the lifespan.
    app.state.bus = Bus(app.state.hub)

    # For the hub's reason too. The courier's loop starts in the lifespan; the
    # object exists here so a route can wake it. The transport is None — the
    # real network — except in a test, which answers in a receiver's place.
    app.state.courier = Courier(Retention.of(settings))
    app.state.hook_transport = None

    # For the courier's reason: the publisher hands it pushes from the first
    # commit, and its loop starts in the lifespan. The sender is None — built
    # from the settings when the loop starts — except in a test, which hands
    # it a fake FCM.
    app.state.pusher = Pusher()
    app.state.push_sender = None

    # jev-docs: which section answers a question, and where a new fact goes.
    # None without a key, which every caller treats as jev being unavailable.
    # Here for the hub's reason: a test replaces it with a fake on an app it
    # built.
    app.state.doc_engine = engine_for(settings)

    # Read by GET /health/requests. Set on app.state, not module-level, so each
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

    # Who has been trying to sign in, and how often (CYLIST-121). On
    # app.state for the hub's reason: a test replaces it so one test's
    # refusals do not spill into the next.
    app.state.sign_in_throttle = SignInThrottle()

    # Added last, so it is the outermost middleware: it times the whole stack
    # including CORS, and it binds the request id before anything inside can
    # log without one.
    app.add_middleware(
        RequestLogMiddleware, api_prefix=API_PREFIX, trusted_proxies=settings.trusted_proxies
    )

    # Outside even that, so the request it logs is already under the base path
    # whichever way it arrived. Absent at the site root, which is production.
    if settings.base_path:
        app.add_middleware(BasePathMiddleware, base_path=settings.base_path)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=API_PREFIX)
    # Before the SPA for the same reason the router is: it claims "/".
    app.state.mcp = mount_mcp(app)

    # Last, because it claims "/": in the production image the built SPA is
    # served from this same process, and in development there is nothing to
    # serve and this does nothing.
    mount_spa(app, settings.web_dir, reserved=API_PREFIX, base_path=settings.base_path)
    return app


app = create_app()
