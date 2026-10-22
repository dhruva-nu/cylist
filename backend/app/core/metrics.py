"""Request counters, in memory, for the status dashboard.

Deliberately not Prometheus or anything that needs a client library and a
scrape config: one process, one dashboard reading one JSON endpoint. Counts
reset when the process restarts, which is why the endpoint also reports
:attr:`Metrics.since` — a rate is meaningless without knowing the window.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.clock import now


class Metrics:
    """Counts of responses by status class, kept for the process lifetime."""

    def __init__(self) -> None:
        self.since: datetime = now()
        self.total = 0
        self.status_2xx = 0
        self.status_3xx = 0
        self.status_4xx = 0
        self.status_5xx = 0
        self.unhandled_500s = 0
        """Requests that raised past every handler. Also in ``status_5xx``; a
        server error answered on purpose (a 503 for the disk) is not a bug and
        is not counted here, which is what makes this the one to alert on."""
        self.deliveries_failed = 0
        """Hook deliveries that ran out of attempts and went ``failed``."""
        self.auth_refusals: Counter[str] = Counter()
        """Refused credentials and sign-ins, by the reason the log gives."""
        self.rate_limit_hits: Counter[str] = Counter()
        """Sign-in attempts the throttle turned away, by which bucket."""
        global _live
        _live = self

    def record(self, status_code: int) -> None:
        self.total += 1
        bucket = status_code // 100
        if bucket == 2:
            self.status_2xx += 1
        elif bucket == 3:
            self.status_3xx += 1
        elif bucket == 4:
            self.status_4xx += 1
        elif bucket == 5:
            self.status_5xx += 1


_live: Metrics
"""The counters of the application most recently built.

Services that run outside a request — the hook courier — have no ``app`` to
read ``app.state.metrics`` from. There is one application per process, so they
count through :func:`live`; a test that builds another app gets fresh counters
because building it replaces this.
"""


def live() -> Metrics:
    return _live


_live = Metrics()


class MetricsMiddleware:
    """Pure-ASGI, not ``BaseHTTPMiddleware``: this runs on every request,
    including every static asset the SPA fetches, so it stays on the fast path
    rather than buffering each response through Starlette's request/response
    wrapping.
    """

    def __init__(self, app: ASGIApp, metrics: Metrics) -> None:
        self.app = app
        self.metrics = metrics

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Nothing inside answered, so no status went past; the response
            # the client will get is the 500 the request log sends outside.
            # Counted here as that 500, or the requests most worth alerting on
            # would be the only ones missing from the dashboard.
            self.metrics.record(status_code or 500)
            self.metrics.unhandled_500s += 1
            raise
        if status_code:
            self.metrics.record(status_code)
