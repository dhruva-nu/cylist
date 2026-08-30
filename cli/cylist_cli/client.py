"""A thin HTTP client for the Cylist API.

Thin on purpose. There is no model layer here and no generated code: every
response is a ``dict`` handed straight to the renderer. The API's shapes are
already documented in its OpenAPI schema, and a second copy of them in this
package would be one more thing to keep in step for no gain — the CLI reads a
handful of fields per response and nothing typed would catch a rename anyway.

What this class does own is the two things every command needs to get right:
the ``Authorization`` header, and turning the API's error envelope into an
:class:`ApiError` instead of an exception the user has to decode.
"""

from __future__ import annotations

from collections.abc import Iterator
from types import TracebackType
from typing import Any, Self

import httpx

from cylist_cli.errors import ApiError, CylistError

API_PREFIX = "/api/v1"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)

JsonDict = dict[str, Any]
JsonList = list[JsonDict]


class Client:
    """A session against one Cylist server."""

    def __init__(self, url: str, token: str, *, transport: httpx.BaseTransport | None = None):
        self.url = url.rstrip("/")
        self._http = httpx.Client(
            base_url=f"{self.url}{API_PREFIX}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json",
                "User-Agent": "cylist-cli",
            },
            timeout=TIMEOUT,
            transport=transport,
            follow_redirects=True,
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    # --- Verbs -------------------------------------------------------------

    def get(self, path: str, **params: Any) -> Any:
        return self._send("GET", path, params=_clean(params))

    def post(self, path: str, body: JsonDict | None = None) -> Any:
        return self._send("POST", path, json=body if body is not None else {})

    def patch(self, path: str, body: JsonDict) -> Any:
        return self._send("PATCH", path, json=body)

    def put(self, path: str, body: JsonDict) -> Any:
        return self._send("PUT", path, json=body)

    def delete(self, path: str) -> Any:
        return self._send("DELETE", path)

    def stream(self, path: str) -> Iterator[bytes]:
        """Download a response body in chunks, without holding it in memory.

        A 50 MB attachment should not become 50 MB of Python string before it
        reaches the file the user asked for.
        """
        try:
            with self._http.stream("GET", path) as response:
                if response.status_code >= httpx.codes.BAD_REQUEST:
                    response.read()
                    raise _api_error(response)
                yield from response.iter_bytes()
        except httpx.RequestError as exc:
            raise _unreachable(self.url, exc) from exc

    # --- Plumbing ----------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            response = self._http.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            raise _unreachable(self.url, exc) from exc

        if response.status_code >= httpx.codes.BAD_REQUEST:
            raise _api_error(response)

        if response.status_code == httpx.codes.NO_CONTENT or not response.content:
            return {}

        try:
            return response.json()
        except ValueError as exc:
            raise CylistError(
                f"The server at {self.url} did not return JSON. "
                "Is CYLIST_URL pointing at the Cylist API?"
            ) from exc


def _clean(params: dict[str, Any]) -> dict[str, Any]:
    """Drop unset query parameters so the server sees its own defaults."""
    return {key: value for key, value in params.items() if value is not None}


def _unreachable(url: str, exc: httpx.RequestError) -> CylistError:
    return CylistError(f"Cannot reach the Cylist API at {url}: {exc}")


def _api_error(response: httpx.Response) -> ApiError:
    """Unwrap ``{"error": {...}}``, falling back when the body is not ours.

    A proxy in front of the API can return an HTML 502 that has never heard of
    the envelope, so the fallback has to be as useful as the real thing.
    """
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
        raw_details = envelope.get("details")
        details = raw_details if isinstance(raw_details, dict) else {}

    if not message:
        message = f"The server returned {status} {response.reason_phrase}."
        if status == httpx.codes.UNAUTHORIZED:
            message = "Not authenticated. Check CYLIST_TOKEN, or run 'cylist login'."

    return ApiError(message, status_code=status, code=code, details=details)
