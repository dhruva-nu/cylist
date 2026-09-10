"""One log line per request, and the id that ties everything else to it.

This is the middleware that makes the rest of the logging worth having. It
does three things a per-request line has to do and that nothing further in
cannot:

* mints the request id — or adopts the caller's — and binds it for the whole
  task, so every record written while serving the request carries it without
  a single service having to be passed one;
* returns that id on the response as ``X-Request-ID``, so a failure somebody
  reports can be found in the file by searching for a string rather than
  guessing at a timestamp;
* times the request and logs its outcome once, at a level graded by status —
  which is what makes ``grep -c WARNING`` a meaningful question to ask of the
  log.

Pure ASGI rather than ``BaseHTTPMiddleware``, for the reason
:mod:`app.core.metrics` gives: it runs on every request including each static
asset the SPA fetches, so it must not buffer responses through Starlette's
request/response wrapping. It also means the timing includes the response
body being written, which is the number that matters for a slow download.
"""

from __future__ import annotations

import logging
import time
from typing import Final

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.logging import (
    REQUEST_ID_HEADER,
    bind_request_id,
    new_request_id,
    reset_request_id,
    sanitise_request_id,
)

logger = logging.getLogger("app.request")

_HEADER_BYTES: Final = REQUEST_ID_HEADER.lower().encode("latin-1")

QUIET_PATHS: Final = frozenset({"/api/v1/health", "/api/v1/health/requests"})
"""Endpoints logged at DEBUG however they turn out.

``docker healthcheck`` hits ``/health`` every thirty seconds forever. At INFO
that is 2,880 lines a day saying nothing happened, which does not merely waste
the file — it buries the twenty lines that matter and rotates them out early.
A health check that starts *failing* still shows up, because a failure is a
4xx or 5xx and those are graded above this.
"""


class RequestLogMiddleware:
    """Correlate, time and record every HTTP request."""

    def __init__(self, app: ASGIApp, *, api_prefix: str) -> None:
        self.app = app
        self._api_prefix = api_prefix

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = sanitise_request_id(_inbound_request_id(scope)) or new_request_id()
        token = bind_request_id(request_id)
        started = time.perf_counter()
        status_code = 0

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                # A new list rather than appending to the one handed over:
                # nothing here owns that list, and a default shared between
                # responses would collect an id per request.
                message["headers"] = [
                    *message.get("headers", []),
                    (_HEADER_BYTES, request_id.encode("latin-1")),
                ]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception:
            # Logged here rather than in an exception handler because this is
            # the only place that still has the request's timing and id in
            # hand. Re-raised untouched: turning the failure into a response
            # is Starlette's job, and swallowing it here would hand the client
            # a 200 with no body.
            logger.exception(
                "%s %s failed",
                _method(scope),
                _path(scope),
                extra={"context": self._context(scope, 500, started)},
            )
            raise
        else:
            self._log_response(scope, status_code, started)
        finally:
            reset_request_id(token)

    def _log_response(self, scope: Scope, status_code: int, started: float) -> None:
        level = self._level(scope, status_code)
        if not logger.isEnabledFor(level):
            return
        logger.log(
            level,
            "%s %s %s",
            _method(scope),
            _path(scope),
            status_code or "no response",
            extra={"context": self._context(scope, status_code, started)},
        )

    def _level(self, scope: Scope, status_code: int) -> int:
        """How loud this request was.

        Graded by status so the level means something operational: ERROR is
        "this deployment is broken", WARNING is "somebody was refused", INFO
        is the ordinary traffic worth keeping.
        """
        if status_code >= 500 or status_code == 0:
            return logging.ERROR
        if status_code >= 400:
            return logging.WARNING
        if _path(scope) in QUIET_PATHS:
            return logging.DEBUG
        if not _path(scope).startswith(self._api_prefix):
            # The SPA's own bundle, fonts and icons. Real traffic, but one page
            # load is dozens of them and none is ever the answer to a question.
            return logging.DEBUG
        return logging.INFO

    def _context(self, scope: Scope, status_code: int, started: float) -> dict[str, object]:
        context: dict[str, object] = {
            "method": _method(scope),
            "path": _path(scope),
            "status": status_code,
            "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        }
        client = _client(scope)
        if client:
            context["client"] = client
        return context


def _inbound_request_id(scope: Scope) -> str | None:
    for name, value in scope.get("headers", []):
        if name == _HEADER_BYTES:
            decoded: str = bytes(value).decode("latin-1", errors="replace")
            return decoded
    return None


def _method(scope: Scope) -> str:
    method: str = scope.get("method", "-")
    return method


def _path(scope: Scope) -> str:
    """The path, and deliberately not the query string.

    ``?q=`` on the search endpoints is whatever somebody typed, which is their
    content and not ours to file away; and a query string is where credentials
    end up by accident in every system that logs them whole.
    """
    path: str = scope.get("path", "-")
    return path


def _client(scope: Scope) -> str | None:
    """The peer's address, which in a deployment is the proxy's.

    Production sits behind ``tailscale serve`` on loopback, so this is
    ``127.0.0.1`` there and says nothing about who called. It is kept anyway
    because in development it is the real client, and because a line with
    ``client=127.0.0.1`` is itself the evidence that a request arrived through
    the proxy rather than around it.

    ``X-Forwarded-For`` is not read: it is a header, so it is whatever the
    caller says it is, and trusting one without a proxy configured to
    overwrite it turns the log into a place anyone can write their own IP
    address.
    """
    client = scope.get("client")
    if not client:
        return None
    host, _port = client
    return str(host)
