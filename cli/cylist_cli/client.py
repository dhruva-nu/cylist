"""A thin HTTP client for the Cylist API.

Thin on purpose. There is no model layer here and no generated code: every
response is a ``dict`` handed straight to the renderer. The API's shapes are
already documented in its OpenAPI schema, and a second copy of them in this
package would be one more thing to keep in step for no gain — the CLI reads a
handful of fields per response and nothing typed would catch a rename anyway.

What this class does own is three things every command needs to get right:
the ``Authorization`` header, turning the API's error envelope into an
:class:`ApiError` instead of an exception the user has to decode, and *which
address* the request goes to.

That last one is why it takes a list. One server is often reachable several
ways (see :mod:`cylist_cli.config`), and a client that cannot connect to the
first tries the next rather than reporting a server that is down when it is
merely somewhere else. Two rules keep that honest:

* **Only a failure to connect moves down the list.** ``ConnectError`` and
  ``ConnectTimeout`` mean the request was never delivered, so sending it
  somewhere else changes nothing. A timeout or a broken socket *after* the
  connection was made might mean a write that was applied and whose response
  was lost, and re-sending a ``POST`` on that basis could create a second
  card. Those fail, as they did before.
* **The one that answers is pinned** for the rest of this client's life and
  remembered for the next command (:mod:`cylist_cli.endpoints`), so a session
  of commands pays the search once.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from time import monotonic
from types import TracebackType
from typing import Any, Self

import httpx

from cylist_cli import endpoints
from cylist_cli.errors import ApiError, CylistError

API_PREFIX = "/api/v1"

TIMEOUT = httpx.Timeout(30.0, connect=10.0)

FAILOVER_TIMEOUT = httpx.Timeout(30.0, connect=3.0)
"""Used instead of :data:`TIMEOUT` when there is more than one address.

Ten seconds is a fair while to wait for the only server you have. It is not a
fair while to wait for the first of three, when the second may be a
millisecond away — an address that has not connected in three seconds is not
the one to be using.
"""

CONNECT_FAILURES = (httpx.ConnectError, httpx.ConnectTimeout)
"""The failures that mean "nothing was delivered", and so may be retried."""

JsonDict = dict[str, Any]
JsonList = list[JsonDict]


class Client:
    """A session against one Cylist server, whatever address it answers on."""

    def __init__(
        self,
        urls: str | Sequence[str],
        token: str | None,
        *,
        transport: httpx.BaseTransport | None = None,
        timeout: httpx.Timeout | None = None,
        budget: float | None = None,
    ):
        """``urls`` is one address or an ordered list of them to try.

        ``token`` is ``None`` for the two things that happen before there is
        one: reading ``/setup``, and exchanging your password for a
        token. No header at all rather than an empty one, because the server
        reads ``Authorization`` in preference to the session cookie — an empty
        bearer would refuse the very request that is trying to log in.

        ``timeout`` overrides the default for a caller that cannot wait — the
        Claude Code hook sits on the path of every prompt, and a server that
        is down must cost it a second, not thirty. ``budget`` is the same
        thought applied to the list: seconds after which no further address is
        tried, so N addresses cannot cost N timeouts.
        """
        candidates = (urls,) if isinstance(urls, str) else tuple(urls)
        self._candidates = tuple(url.rstrip("/") for url in candidates if url) or ("",)
        self._budget = budget
        self._position = 0
        self.url = self._candidates[0]

        headers = {"Accept": "application/json", "User-Agent": "cylist-cli"}
        if token:
            headers["Authorization"] = f"Bearer {token}"

        default = TIMEOUT if len(self._candidates) == 1 else FAILOVER_TIMEOUT
        self._http = httpx.Client(
            base_url=f"{self.url}{API_PREFIX}",
            headers=headers,
            timeout=timeout or default,
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
        attempt = _Attempt(self)
        while attempt.next_address():
            try:
                with self._http.stream("GET", path) as response:
                    if response.status_code >= httpx.codes.BAD_REQUEST:
                        response.read()
                        raise _api_error(response)
                    attempt.succeeded()
                    yield from response.iter_bytes()
                    return
            except CONNECT_FAILURES as exc:
                # Before a single byte arrived, so nothing has been yielded and
                # starting again elsewhere is safe.
                attempt.failed(exc)
            except httpx.RequestError as exc:
                raise _unreachable(self.url, exc) from exc
        raise attempt.give_up()

    # --- Plumbing ----------------------------------------------------------

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        attempt = _Attempt(self)
        while attempt.next_address():
            try:
                response = self._http.request(method, path, **kwargs)
            except CONNECT_FAILURES as exc:
                attempt.failed(exc)
                continue
            except httpx.RequestError as exc:
                raise _unreachable(self.url, exc) from exc
            attempt.succeeded()
            return self._unwrap(response)
        raise attempt.give_up()

    def _unwrap(self, response: httpx.Response) -> Any:
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

    def _point_at(self, position: int) -> None:
        self._position = position
        self.url = self._candidates[position]
        self._http.base_url = f"{self.url}{API_PREFIX}"


class _Attempt:
    """One request's walk down the list of addresses.

    A small object rather than a loop variable because the walk has three
    pieces of state — where it has got to, what each address said, and when it
    started — and a request that succeeds on the second address has to pin it
    for the next request, which a local variable cannot do.
    """

    def __init__(self, client: Client) -> None:
        self._client = client
        self._position = client._position - 1
        self._started = monotonic()
        self._refused: list[str] = []

    def next_address(self) -> bool:
        """Point the client at the next address, or say there is not one."""
        self._position += 1
        if self._position >= len(self._client._candidates):
            return False
        if self._refused and self._out_of_time():
            return False
        self._client._point_at(self._position)
        return True

    def succeeded(self) -> None:
        """Pin the address that answered, here and for the next command."""
        self._client._position = self._position
        endpoints.remember(self._client.url)

    def failed(self, exc: httpx.ConnectError | httpx.ConnectTimeout) -> None:
        self._refused.append(f"{self._client.url} ({_why(exc)})")

    def give_up(self) -> CylistError:
        if len(self._refused) == 1:
            return CylistError(f"Cannot reach the Cylist API at {self._client.url}.")
        return CylistError("Cannot reach the Cylist API. Tried " + ", ".join(self._refused) + ".")

    def _out_of_time(self) -> bool:
        budget = self._client._budget
        return budget is not None and monotonic() - self._started >= budget


def _why(exc: httpx.RequestError) -> str:
    """The reason an address was no good, in as few words as it takes."""
    return str(exc) or type(exc).__name__


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
            message = "Not authenticated. Check CYLIST_TOKEN, or run 'cylist setup'."

    return ApiError(message, status_code=status, code=code, details=details)
