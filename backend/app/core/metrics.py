"""Request counters, in memory, for the status dashboard.

Deliberately not Prometheus or anything that needs a client library and a
scrape config: one process, one dashboard reading one JSON endpoint. Counts
reset when the process restarts, which is why the endpoint also reports
:attr:`Metrics.since` — a rate is meaningless without knowing the window.
"""

from __future__ import annotations

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
            raise
        if status_code:
            self.metrics.record(status_code)
