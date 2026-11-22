"""Prometheus metrics, served on a port of their own (CYLIST-195).

Off unless ``CYLIST_METRICS_ENABLED`` is set. On, the app counts every request
by method, route and status, and a second listener started in the lifespan
answers ``GET /metrics`` on ``CYLIST_METRICS_PORT``. The app's own port never
serves it: that port is what the tunnel and the ingress publish, and the
numbers here — route names, pool sizes, disk space — are for the operator, not
the internet. The SPA is told to 404 the path rather than answer it with
``index.html`` (see :func:`app.spa.mount_spa`), so a probe for it sees nothing
there either.

One process, one worker, so one plain registry and no multiprocess mode. Each
app gets a registry of its own rather than the library's global one, so an app
a test builds starts from zero and cannot collide with the last one's metrics.

Most values are read when Prometheus asks rather than kept up to date as they
change — the hub's sockets, the pool, the ``LISTEN`` line, the disk, and the
counters :mod:`app.core.metrics` already keeps for ``/health/requests``. Only
what cannot be read on demand is pushed: the requests themselves, the reaper's
last pass, and the event loop's lag, which can only be seen by a task that
waits on the loop and notices it was late.
"""

from __future__ import annotations

import asyncio
import shutil
import socket
import time
from collections.abc import AsyncIterator, Callable, Generator, Iterator, Mapping, Sequence
from contextlib import asynccontextmanager, contextmanager, suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Final, NoReturn, Protocol

import uvicorn
from fastapi.routing import iter_route_contexts
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    ProcessCollector,
    generate_latest,
)
from prometheus_client.core import (
    CounterMetricFamily,
    GaugeMetricFamily,
    Metric,
    SummaryMetricFamily,
)
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import BaseRoute, Match, Mount, Route
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import Metrics

METRICS_PATH: Final = "/metrics"

UNMATCHED: Final = "unmatched"
"""The route label of a request no route claimed. One label for all of them:
the path itself is whatever a scanner felt like typing, and a label per typo
is a time series per typo."""

OTHER_METHOD: Final = "other"

_METHODS: Final = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
"""Methods labelled as themselves. Anything else is :data:`OTHER_METHOD`, for
the same reason as :data:`UNMATCHED`: the method is the client's to make up."""

LOOP_LAG_EVERY: Final = 1.0
"""Seconds between the lag sampler's wake-ups."""


class PoolReading(Protocol):
    """What the collector reads of the database pool — :class:`app.db.PoolStatus`."""

    @property
    def size(self) -> int: ...
    @property
    def max_overflow(self) -> int: ...
    @property
    def in_use(self) -> int: ...
    @property
    def waiting(self) -> int: ...
    @property
    def waits(self) -> int: ...
    @property
    def wait_seconds(self) -> float: ...


@dataclass(frozen=True)
class Sources:
    """Where each value read at scrape time comes from.

    Callables rather than the objects themselves, because the objects are the
    app's to replace: the database exists only once the lifespan has run, and
    a test swaps the hub and the counters for fresh ones between tests.
    """

    counters: Callable[[], Metrics]
    realtime: Callable[[], Mapping[str, int]]
    """The hub's :meth:`~app.realtime.hub.Hub.stats`."""
    pool: Callable[[], PoolReading | None]
    """``None`` before the lifespan has opened the pool."""
    listening: Callable[[], bool]
    """Whether the bus's ``LISTEN`` connection is up."""
    blob_dir: Callable[[], Path]


class Prometheus:
    """One app's metrics: the registry, and the handful that are pushed."""

    def __init__(self, sources: Sources) -> None:
        self.registry = CollectorRegistry()
        self.requests = Counter(
            "http_requests",
            "HTTP responses, by method, route template and status.",
            ["method", "route", "status"],
            registry=self.registry,
        )
        self.duration = Histogram(
            "http_request_duration_seconds",
            "Time from a request arriving to its response's last byte.",
            ["method", "route"],
            registry=self.registry,
        )
        self.in_flight = Gauge(
            "http_requests_in_flight",
            "HTTP requests being served right now.",
            registry=self.registry,
        )
        self.loop_lag = Gauge(
            "cylist_event_loop_lag_seconds",
            "How late the event loop last woke a task that asked to sleep a second.",
            registry=self.registry,
        )
        self.reaper_last_run = Gauge(
            "cylist_reaper_last_run_timestamp_seconds",
            "When the agent-session reaper last finished a pass, as a Unix time. "
            "Zero until it has.",
            registry=self.registry,
        )
        self.registry.register(_Readings(sources))
        # CPU, memory and open files, which the global registry would have
        # had for free.
        ProcessCollector(registry=self.registry)

    def exposition(self) -> bytes:
        """The registry in Prometheus's text format, as ``/metrics`` serves it."""
        return generate_latest(self.registry)

    def observe(self, method: str, route: str, status: int, seconds: float) -> None:
        self.requests.labels(method, route, str(status)).inc()
        self.duration.labels(method, route).observe(seconds)

    async def watch_the_loop(self, every: float = LOOP_LAG_EVERY) -> NoReturn:
        """Sleep, and record how much longer than asked the sleep took.

        A loop that something is blocking — a synchronous call, a long CPU
        stretch — wakes this late by exactly as long as it was blocked.
        """
        loop = asyncio.get_running_loop()
        while True:
            asked = loop.time()
            await asyncio.sleep(every)
            self.loop_lag.set(max(0.0, loop.time() - asked - every))


class _Readings:
    """The values read from the app at scrape time."""

    def __init__(self, sources: Sources) -> None:
        self._sources = sources

    def collect(self) -> Iterator[Metric]:
        yield from self._counters(self._sources.counters())
        yield from self._realtime(self._sources.realtime())
        pool = self._sources.pool()
        if pool is not None:
            yield from self._pool(pool)
        yield GaugeMetricFamily(
            "cylist_listen_connection_up",
            "1 while the event bus's LISTEN connection to Postgres is up, else 0.",
            value=1 if self._sources.listening() else 0,
        )
        try:
            free = shutil.disk_usage(self._sources.blob_dir()).free
        except OSError:
            # Not there yet (the lifespan makes it), or the volume has gone:
            # absent is more honest than a zero that reads as a full disk.
            return
        yield GaugeMetricFamily(
            "cylist_blob_store_free_bytes",
            "Free space on the filesystem holding uploaded files.",
            value=free,
        )

    @staticmethod
    def _counters(counters: Metrics) -> Iterator[Metric]:
        yield CounterMetricFamily(
            "cylist_unhandled_500",
            "Requests that raised past every handler and were answered with a 500.",
            value=counters.unhandled_500s,
        )
        yield CounterMetricFamily(
            "cylist_hook_deliveries_failed",
            "Hook deliveries that ran out of attempts.",
            value=counters.deliveries_failed,
        )
        refusals = CounterMetricFamily(
            "cylist_auth_refusals",
            "Refused credentials and sign-ins, by reason.",
            labels=["reason"],
        )
        for reason, count in sorted(counters.auth_refusals.items()):
            refusals.add_metric([reason], count)
        yield refusals
        hits = CounterMetricFamily(
            "cylist_rate_limit_hits",
            "Sign-in attempts the throttle turned away, by bucket.",
            labels=["bucket"],
        )
        for bucket, count in sorted(counters.rate_limit_hits.items()):
            hits.add_metric([bucket], count)
        yield hits

    @staticmethod
    def _realtime(stats: Mapping[str, int]) -> Iterator[Metric]:
        yield GaugeMetricFamily(
            "cylist_ws_agent_connections",
            "Agent sockets this process holds.",
            value=stats["agent_sockets"],
        )
        yield GaugeMetricFamily(
            "cylist_ws_board_watchers",
            "Board sockets this process holds, across every project.",
            value=stats["board_watchers"],
        )

    @staticmethod
    def _pool(pool: PoolReading) -> Iterator[Metric]:
        yield GaugeMetricFamily(
            "cylist_db_pool_size",
            "Connections the pool keeps open, at most.",
            value=pool.size,
        )
        yield GaugeMetricFamily(
            "cylist_db_pool_max_overflow",
            "Connections the pool may lend beyond its size under load.",
            value=pool.max_overflow,
        )
        yield GaugeMetricFamily(
            "cylist_db_pool_in_use",
            "Connections checked out of the pool right now, overflow included.",
            value=pool.in_use,
        )
        yield GaugeMetricFamily(
            "cylist_db_pool_waiting",
            "Checkouts waiting for a connection right now.",
            value=pool.waiting,
        )
        yield SummaryMetricFamily(
            "cylist_db_pool_wait_seconds",
            "Time spent getting a connection from the pool, opening one included.",
            count_value=pool.waits,
            sum_value=pool.wait_seconds,
        )


def route_template(route: BaseRoute) -> str | None:
    """The label for a route: its template, never the path it was asked by."""
    if isinstance(route, Mount):
        return f"{route.path}/{{path:path}}"
    if isinstance(route, Route):
        return route.path
    return None


class PrometheusMiddleware:
    """Counts and times every HTTP request, labelled by the route that took it.

    Pure ASGI, for the reason :class:`app.core.metrics.MetricsMiddleware` is.

    The route is read after the request, from what routing left in the scope:
    FastAPI puts the matched route there itself. That is the route as its own
    router declared it, though — ``/health``, not ``/api/v1/health`` — so the
    template it is served under is looked up from FastAPI's walk of the
    included routers, the one its OpenAPI document is built from (as
    :class:`app.core.body_limit.BodyLimitMiddleware` does). Starlette's own
    routes and mounts — ``/mcp``, the SPA — leave nothing in the scope, so for
    those the top-level routes are matched again, the way the router did.
    """

    def __init__(
        self, app: ASGIApp, *, prometheus: Prometheus, routes: Sequence[BaseRoute]
    ) -> None:
        self.app = app
        self.prometheus = prometheus
        self.routes = routes
        # Read when the stack is first built, by which time every router is
        # included. By identity: a route is the same object for the life of
        # the app, and is not necessarily hashable.
        self._templates: dict[int, str] = {
            id(context.original_route): context.path
            for context in iter_route_contexts(routes)
            if context.path is not None and not isinstance(context.original_route, Mount)
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        method = scope["method"] if scope["method"] in _METHODS else OTHER_METHOD
        # Before routing changes it: a mount moves its prefix into root_path.
        as_asked: Scope = {
            "type": "http",
            "method": scope["method"],
            "path": scope["path"],
            "root_path": scope.get("root_path", ""),
        }
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        self.prometheus.in_flight.inc()
        started = time.perf_counter()
        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Answered outside with a 500 — see MetricsMiddleware.
            status_code = status_code or 500
            raise
        finally:
            self.prometheus.in_flight.dec()
            if status_code:
                self.prometheus.observe(
                    method,
                    self._route(scope, as_asked),
                    status_code,
                    time.perf_counter() - started,
                )

    def _route(self, scope: Scope, as_asked: Scope) -> str:
        routed = scope.get("route")
        if routed is not None and (template := self._templates.get(id(routed))):
            return template
        for route in self.routes:
            match, _ = route.matches(as_asked)
            if match is not Match.NONE:
                return route_template(route) or UNMATCHED
        return UNMATCHED


def exposition_app(prometheus: Prometheus) -> Starlette:
    """The metrics listener's whole app: ``GET /metrics``, and 404 for the rest."""

    async def metrics(_: Request) -> Response:
        return Response(prometheus.exposition(), media_type=CONTENT_TYPE_LATEST)

    return Starlette(routes=[Route(METRICS_PATH, metrics, methods=["GET"])])


class _Listener(uvicorn.Server):
    """A uvicorn server that leaves the process's signals alone.

    The app's own server owns SIGTERM and SIGINT: it shuts down, the lifespan
    ends, and the lifespan stops this. A second server capturing them too
    would replace the first one's handlers for as long as it ran.
    """

    @contextmanager
    def capture_signals(self) -> Generator[None]:
        yield


@asynccontextmanager
async def serving(prometheus: Prometheus, *, host: str, port: int) -> AsyncIterator[int]:
    """Serve ``/metrics`` on ``host:port`` and sample the loop's lag until exit.

    Yields the port actually bound, which is ``port`` unless that was zero.
    The socket is bound here, before anything is started, so a port already
    taken fails the app's startup with the address in the error rather than
    inside a task nobody is watching.
    """
    sock = socket.create_server((host, port))
    bound: int = sock.getsockname()[1]
    server = _Listener(
        uvicorn.Config(
            exposition_app(prometheus),
            lifespan="off",
            access_log=False,
            # The app has configured logging already; uvicorn's defaults would
            # configure it again, over the top.
            log_config=None,
        )
    )
    listener = asyncio.create_task(server.serve(sockets=[sock]), name="cylist-metrics")
    lag = asyncio.create_task(prometheus.watch_the_loop(), name="cylist-loop-lag")
    try:
        yield bound
    finally:
        lag.cancel()
        with suppress(asyncio.CancelledError):
            await lag
        server.should_exit = True
        await listener
        sock.close()
