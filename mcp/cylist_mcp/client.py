"""An async HTTP client for the Cylist API.

The MCP SDK is asyncio all the way down, so this is ``httpx.AsyncClient`` where
the CLI's equivalent is the synchronous one. The two are deliberately separate
files in separate packages rather than a shared library: between them they are
about sixty lines of ``Authorization`` header and error unwrapping, and a third
distributable to keep in step would cost more than the duplication does.

It takes a *list* of addresses. A server is often reachable more than one way
(see :mod:`cylist_mcp.config`), and this process outlives the network it
started on by hours — a laptop that suspends on a tailnet and wakes on a hotel
wifi would otherwise spend the rest of the session answering every tool call
with "cannot reach the Cylist API". So a request that cannot *connect* is
tried at the next address, and the one that answers is kept.

Only a failure to connect is retried. A timeout or a dropped socket after the
connection was made may mean a write that was applied and whose response was
lost, and re-sending it could create a second card.
"""

from __future__ import annotations

from collections.abc import Sequence
from types import TracebackType
from typing import Any, Self

import httpx

from cylist_mcp.errors import CylistError

API_PREFIX = "/api/v1"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)

FAILOVER_TIMEOUT = httpx.Timeout(30.0, connect=3.0)
"""Used instead of :data:`TIMEOUT` when there is more than one address.

Ten seconds is a fair while to wait for the only server there is. It is not a
fair while to wait for the first of three when the second may be a
millisecond away, and a tool call the model is waiting on is the wrong place
to spend it.
"""

CONNECT_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout)
"""The failures that mean "nothing was delivered", and so may be retried."""


class ApiClient:
    """A session against one Cylist server, whatever address it answers on."""

    def __init__(
        self,
        urls: str | Sequence[str],
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        candidates = (urls,) if isinstance(urls, str) else tuple(urls)
        self._candidates = tuple(url.rstrip("/") for url in candidates if url) or ("",)
        self._position = 0
        self.url = self._candidates[0]
        self._http = httpx.AsyncClient(
            base_url=f"{self.url}{API_PREFIX}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "cylist-mcp",
            },
            timeout=TIMEOUT if len(self._candidates) == 1 else FAILOVER_TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._http.aclose()

    async def get(self, path: str, **params: Any) -> Any:
        clean = {key: value for key, value in params.items() if value is not None}
        return await self._send("GET", path, params=clean)

    async def get_text(self, path: str, *, max_chars: int) -> tuple[str, bool]:
        """Fetch a path as text, for a download rather than a JSON document.

        Returns the text and whether it was cut short. Capped because the other
        end of this is a model's context window: a skill is meant to be a page
        of instructions, and one that turns out to be a megabyte should arrive
        truncated with a note rather than filling the window.
        """
        response = await self._attempt("GET", path, headers={"Accept": "*/*"})
        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise _api_error(response)

        body = response.text
        if len(body) > max_chars:
            return body[:max_chars], True
        return body, False

    async def post(self, path: str, body: dict[str, Any] | None = None) -> Any:
        return await self._send("POST", path, json=body if body is not None else {})

    async def patch(self, path: str, body: dict[str, Any]) -> Any:
        return await self._send("PATCH", path, json=body)

    async def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        response = await self._attempt(method, path, **kwargs)

        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise _api_error(response)

        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise CylistError(
                f"The server at {self.url} did not return JSON.", code="bad_response"
            ) from exc

    async def _attempt(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        """Send the request, walking down the addresses until one connects.

        The address that answers is kept for every request after this one, so
        a session pays the search once rather than per call.
        """
        refused: list[str] = []
        for position in range(self._position, len(self._candidates)):
            self._point_at(position)
            try:
                response = await self._http.request(method, path, **kwargs)
            except CONNECT_FAILURES as exc:
                refused.append(f"{self.url} ({exc})")
                continue
            except httpx.RequestError as exc:
                raise CylistError(
                    f"Cannot reach the Cylist API at {self.url}: {exc}",
                    code="unreachable",
                ) from exc
            self._position = position
            return response

        raise CylistError(
            "Cannot reach the Cylist API. Tried " + ", ".join(refused) + ".",
            code="unreachable",
        )

    def _point_at(self, position: int) -> None:
        self.url = self._candidates[position]
        self._http.base_url = f"{self.url}{API_PREFIX}"


def _api_error(response: httpx.Response) -> CylistError:
    """Unwrap ``{"error": {...}}`` into something worth showing a model."""
    status = response.status_code
    code, message, details = f"http_{status}", "", {}

    try:
        body = response.json()
    except ValueError:
        body = None

    if isinstance(body, dict) and isinstance(body.get("error"), dict):
        envelope = body["error"]
        code = str(envelope.get("code") or code)
        message = str(envelope.get("message") or "")
        raw = envelope.get("details")
        details = raw if isinstance(raw, dict) else {}

    if not message:
        message = f"The Cylist API returned {status} {response.reason_phrase}."

    if status == httpx.codes.FORBIDDEN:
        message += (
            " The configured token does not carry the scope this needs; "
            "it has to be reissued rather than retried."
        )
    elif status == httpx.codes.UNAUTHORIZED:
        message += (
            " The configured token is missing, expired or revoked — CYLIST_TOKEN, or the"
            " one 'cylist setup' wrote to ~/.config/cylist/config.toml."
        )

    return CylistError(message, code=code, status_code=status, details=details)
