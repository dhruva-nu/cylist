"""The headers every response carries for the browser's sake (CYLIST-136).

None of these change what the API answers; they change what a browser will
do with the answer:

* ``X-Content-Type-Options: nosniff`` — a response is the type it says it
  is. Without it a browser may decide a download "looks like" HTML or script
  and run it.
* ``X-Frame-Options: DENY`` and ``frame-ancestors 'none'`` — no page may put
  Cylist in a frame, which is what clickjacking needs.
* ``Referrer-Policy: strict-origin-when-cross-origin`` — a link out of a doc
  tells the site it leads to that it came from Cylist, not which project's
  page.
* ``Content-Security-Policy`` — the SPA runs only its own bundle. See
  :func:`spa_policy` for what it does load from elsewhere, and why.
* ``Strict-Transport-Security`` — deployed stacks only. They are reached
  over HTTPS alone; someone's own machine is ``http://localhost``, where the
  header would be a promise nothing keeps.

A response that already set one of these keeps its own: a download sets a
stricter policy than the page's (see :data:`DOWNLOAD_HEADERS`), and the
middleware fills in only what is missing.

Pure ASGI, for the reason :mod:`app.core.metrics` gives.
"""

from __future__ import annotations

from typing import Final

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.base_path import route_path

HSTS: Final = "max-age=31536000"
"""A year, and no ``includeSubDomains``: this header is sent by every stack,
and a stack does not get to make promises about hosts it does not serve."""

DOWNLOAD_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    # A restricted file must not sit in a shared cache, or in the browser's
    # back/forward cache for the next person at the machine to find.
    "Cache-Control": "private, no-store",
    # A download is somebody's bytes, not a page of ours: should a browser
    # render one despite the attachment disposition, it runs nothing and
    # reaches nothing.
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
"""What a response carrying an uploaded file's bytes adds — a file or a skill."""


def spa_policy(*, allow_plain_sockets: bool) -> str:
    """The Content-Security-Policy for the SPA, and so for everything else.

    Its scripts and styles are the built bundle, served from here; nothing
    is inline. What it loads from elsewhere:

    * Google Fonts — the stylesheet from ``fonts.googleapis.com`` and the
      font files it names from ``fonts.gstatic.com`` (``index.html``);
    * images from any HTTPS origin, because a doc or a card's description is
      markdown and may show one; ``data:`` for the icons the stylesheet
      inlines, ``blob:`` for anything the page draws itself;
    * the board's WebSocket, which ``'self'`` covers in current browsers and
      ``wss:`` covers in the ones that read ``'self'`` as HTTP only. Plain
      ``ws:`` only off a deployment, where the page itself is plain HTTP.
    """
    sockets = "wss: ws:" if allow_plain_sockets else "wss:"
    return "; ".join(
        (
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' https://fonts.googleapis.com",
            "font-src 'self' https://fonts.gstatic.com",
            "img-src 'self' data: blob: https:",
            f"connect-src 'self' {sockets}",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        )
    )


DOCS_POLICY: Final = "; ".join(
    (
        "default-src 'self'",
        # FastAPI's Swagger UI page: its bundle and stylesheet from jsDelivr,
        # and one inline script that starts it. Inline is allowed on this
        # page alone, which shows nothing but the API's own schema.
        "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net",
        "img-src 'self' data: https:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'self'",
        "frame-ancestors 'none'",
    )
)
"""The policy for the interactive API docs, which the SPA's would break."""


class SecurityHeadersMiddleware:
    """Add the browser-facing security headers to every HTTP response.

    Args:
        app: The application inside.
        hsts: Whether to send ``Strict-Transport-Security`` — a deployed
            stack, which is only ever reached over HTTPS.
        docs_paths: The paths of the API's own docs pages, which get
            :data:`DOCS_POLICY` in place of the SPA's.
    """

    def __init__(self, app: ASGIApp, *, hsts: bool, docs_paths: frozenset[str]) -> None:
        self.app = app
        self._docs_paths = docs_paths
        common = {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
        }
        if hsts:
            common["Strict-Transport-Security"] = HSTS
        self._common = common
        self._spa_policy = spa_policy(allow_plain_sockets=not hsts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        policy = DOCS_POLICY if route_path(scope) in self._docs_paths else self._spa_policy

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self._common.items():
                    headers.setdefault(name, value)
                headers.setdefault("Content-Security-Policy", policy)
            await send(message)

        await self.app(scope, receive, send_with_headers)
