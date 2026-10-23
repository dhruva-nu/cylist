"""A cap on every request body, enforced before the body is read (CYLIST-123).

Nothing used to bound a body except the blob store, and that only while
copying an upload out of the spool it had already been written to. A body is
now refused at the door:

* one that declares a ``Content-Length`` over its route's limit is answered
  413 without a byte of it being read;
* one that does not — ``Transfer-Encoding: chunked`` — is counted as it is
  received, and the request is answered 413 the moment the count passes the
  limit, whatever the route was doing with it.

Two limits. A route marked with :func:`streams_upload` may take the configured
upload size plus room for the form around the file; everything else takes
``CYLIST_MAX_REQUEST_KB``, which is plenty for any JSON this API accepts.

Pure ASGI rather than ``BaseHTTPMiddleware``, for the reason
:mod:`app.core.metrics` gives, and because it has to sit between the server
and the route's ``receive`` — which is the only place a chunked body can be
counted before something else has buffered it.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from typing import Any, Final

from fastapi.routing import iter_route_contexts
from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.routing import BaseRoute, compile_path
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.base_path import route_path
from app.core.errors import PayloadTooLargeError, error_body

FORM_OVERHEAD_BYTES: Final = 1024 * 1024
"""What an upload route allows beyond the file itself: the multipart
boundaries, each part's headers, and the small text fields beside the file.
A skill's folder of 200 parts spends a few tens of kilobytes on this."""

_UPLOAD_MARK: Final = "__cylist_streams_upload__"


def streams_upload[Endpoint: Callable[..., Any]](endpoint: Endpoint) -> Endpoint:
    """Mark a route as one that takes an upload, and so the larger limit.

    A mark on the endpoint rather than a list of paths kept here, so the route
    that reads the body is the one that says how large it may be, and a path
    renamed in the router cannot leave a stale pattern behind.
    """
    setattr(endpoint, _UPLOAD_MARK, True)
    return endpoint


class _BodyTooLargeError(Exception):
    """Raised out of ``receive`` once a streamed body passes its limit.

    Private, and caught by the middleware that raised it: whoever was reading
    may have wrapped it — FastAPI turns an error while reading a JSON body
    into a 400 — so the middleware answers on the strength of its own flag
    rather than of whatever arrives back.
    """


class BodyLimitMiddleware:
    """Refuse a request body larger than its route allows.

    Args:
        app: The application inside.
        routes: The application's routes — the live list, read when the
            stack is first built, by which time every router is included.
        upload_bytes: The largest file an upload route takes.
        request_bytes: The largest body any other route takes.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        routes: Sequence[BaseRoute],
        upload_bytes: int,
        request_bytes: int,
    ) -> None:
        self.app = app
        # Through FastAPI's own walk of the routes — the one its OpenAPI
        # document is built from — because an included router is not
        # flattened into the app's list: its routes, and the prefix they are
        # served under, are found only by descending into it.
        self._uploads: list[tuple[re.Pattern[str], frozenset[str]]] = [
            (compile_path(context.path)[0], frozenset(context.methods or ()))
            for context in iter_route_contexts(routes)
            if context.path is not None and getattr(context.endpoint, _UPLOAD_MARK, False)
        ]
        self._upload_limit = upload_bytes + FORM_OVERHEAD_BYTES
        self._request_limit = request_bytes

    def limit_for(self, scope: Scope) -> int:
        """The most this request's body may hold."""
        path = route_path(scope)
        method: str = scope["method"]
        for pattern, methods in self._uploads:
            if method in methods and pattern.match(path):
                return self._upload_limit
        return self._request_limit

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        limit = self.limit_for(scope)
        declared = Headers(scope=scope).get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            # Answered without calling the app at all: no route, no
            # dependency, no parser has the chance to read a byte.
            await _refuse(limit)(scope, receive, send)
            return

        received = 0
        exceeded = False
        started = False

        async def counting_receive() -> Message:
            nonlocal received, exceeded
            if exceeded:
                raise _BodyTooLargeError
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    exceeded = True
                    raise _BodyTooLargeError
            return message

        async def guarded_send(message: Message) -> None:
            nonlocal started
            if exceeded and not started:
                # Whatever the app made of the refusal — a 400 from FastAPI's
                # body reader, a 500 from somewhere less careful — the client
                # is owed the 413, and gets it instead.
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, guarded_send)
        except Exception:
            if not exceeded:
                raise
        if exceeded and not started:
            await _refuse(limit)(scope, receive, send)


def _refuse(limit: int) -> JSONResponse:
    """The 413, in the envelope every other refusal comes in."""
    error = PayloadTooLargeError(
        f"That request is larger than the {_size(limit)} this endpoint accepts.",
        details={"max_bytes": limit},
    )
    return JSONResponse(
        status_code=error.status_code,
        content=error_body(error.code, error.message, error.details),
        # The rest of the body is still on its way, and nothing is going to
        # read it: closing is cheaper than draining it to keep the line open.
        headers={"Connection": "close"},
    )


def _size(limit: int) -> str:
    megabytes = limit / (1024 * 1024)
    return f"{megabytes:.0f} MB" if megabytes >= 1 else f"{limit // 1024} KB"
