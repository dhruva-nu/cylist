"""An async HTTP client for the Cylist API.

The MCP SDK is asyncio all the way down, so this is ``httpx.AsyncClient`` where
the CLI's equivalent is the synchronous one. The two are deliberately separate
files in separate packages rather than a shared library: between them they are
about sixty lines of ``Authorization`` header and error unwrapping, and a third
distributable to keep in step would cost more than the duplication does.
"""

from __future__ import annotations

from types import TracebackType
from typing import Any, Self

import httpx

from cylist_mcp.errors import CylistError

API_PREFIX = "/api/v1"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class ApiClient:
    """A session against one Cylist server."""

    def __init__(self, url: str, token: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.url = url.rstrip("/")
        self._http = httpx.AsyncClient(
            base_url=f"{self.url}{API_PREFIX}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "cylist-mcp",
            },
            timeout=TIMEOUT,
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
        try:
            response = await self._http.get(path, headers={"Accept": "*/*"})
        except httpx.RequestError as exc:
            raise CylistError(
                f"Cannot reach the Cylist API at {self.url}: {exc}",
                code="unreachable",
            ) from exc

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
        try:
            response = await self._http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise CylistError(
                f"Cannot reach the Cylist API at {self.url}: {exc}",
                code="unreachable",
            ) from exc

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
        message += " Check CYLIST_TOKEN — it is missing, expired or revoked."

    return CylistError(message, code=code, status_code=status, details=details)
