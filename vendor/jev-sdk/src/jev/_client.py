"""HTTP clients for the Jev System One API: `Jev` (sync) and `AsyncJev` (asyncio).

    jev = Jev()                                       # api_key defaults to $JEV_API_KEY
    result = jev.ask(ticket, Department, Urgency, ChurnRisk)
    result[Department].choice, result[Urgency].score, result[ChurnRisk].probability

    jev.system_one(ticket, {"spam": {"type": "noul", "instructions": "Is this spam?"}})   # raw dicts
    jev.models()                                      # [Model(name="jev-latest", ...), ...]

Both retry 408 / 409 / 429 / 5xx and network errors with capped exponential backoff,
honouring `Retry-After` / `Retry-After-Ms`. Every call is reported to the client's observers
(see jev.observability) as one RequestEvent, retries included.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Type, TypeVar, Union

import httpx

from ._answers import Model, Result, parse_models, parse_result
from ._errors import JevConnectionError, JevResponseError, JevTimeoutError, error_for_status
from ._questions import JSON, Question
from ._version import __version__
from .observability._core import Observe, Observer, RequestEvent, emit, resolve_observers, scoped_observers

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
DEFAULT_TIMEOUT = 30.0
DEFAULT_MAX_RETRIES = 2

RETRY_STATUSES = frozenset({408, 409, 429} | set(range(500, 600)))  # 529 = overloaded
MAX_RETRY_AFTER = 60.0  # never sleep longer than this on a server's say-so
REQUEST_ID_HEADER = "x-typesafe-request-id"

State = Union[str, Mapping[str, Any], Sequence[Any]]
Timeout = Union[float, httpx.Timeout, None]
T = TypeVar("T")
# Turns (response body, request id) into what the public method returns.
Parse = Callable[[Any, Optional[str]], T]

# Indirection so tests can skip the waiting.
_sleep = time.sleep
_async_sleep = asyncio.sleep


class _Call:
    """Bookkeeping for one logical call across its attempts; becomes a RequestEvent at the end."""

    def __init__(self, operation: str, method: str, url: str, body: Any):
        self.operation = operation
        self.method = method
        self.url = url
        self.model = body.get("model") if isinstance(body, dict) else None
        self.questions = tuple(body.get("questions") or ()) if isinstance(body, dict) else ()
        self.started_at = time.time()
        self._t0 = time.perf_counter()
        self._attempt_t0 = self._t0
        self.attempts = 0
        self.latency: Optional[float] = None
        self.response: Optional[httpx.Response] = None
        self.payload: Any = None

    def attempt(self) -> None:
        self.attempts += 1
        self._attempt_t0 = time.perf_counter()

    def attempted(self, response: Optional[httpx.Response]) -> None:
        self.latency = time.perf_counter() - self._attempt_t0
        self.response = response

    def event(self, error: Optional[BaseException] = None) -> RequestEvent:
        usage = self.payload.get("usage") if isinstance(self.payload, dict) else None
        usage = usage if isinstance(usage, dict) else {}
        response_model = self.payload.get("model") if isinstance(self.payload, dict) else None
        response = self.response
        return RequestEvent(
            operation=self.operation,
            method=self.method,
            url=self.url,
            model=self.model,
            response_model=response_model if isinstance(response_model, str) else None,
            questions=self.questions,
            status_code=response.status_code if response is not None else None,
            request_id=response.headers.get(REQUEST_ID_HEADER) if response is not None else None,
            attempts=self.attempts,
            started_at=self.started_at,
            duration=time.perf_counter() - self._t0,
            latency=self.latency,
            input_tokens=_int_or_none(usage.get("input_tokens")),
            output_tokens=_int_or_none(usage.get("output_tokens")),
            error=error,
        )


class _BaseClient:
    def __init__(
        self,
        api_key: Optional[str],
        model: str,
        base_url: Optional[str],
        timeout: Timeout,
        max_retries: int,
        default_headers: Optional[Mapping[str, str]],
        observe: Observe,
    ):
        api_key = api_key or os.environ.get("JEV_API_KEY")
        if not api_key:
            raise ValueError("no Jev API key: pass api_key= or set JEV_API_KEY")
        if max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        self.api_key = api_key
        self.model = model
        self.base_url = (base_url or os.environ.get("JEV_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        # Mutable on purpose: `jev.observers.append(...)` attaches one later.
        self.observers: List[Observer] = resolve_observers(observe)
        self._headers = {
            "Authorization": "Bearer %s" % api_key,
            "Accept": "application/json",
            "User-Agent": "jev-python/%s" % __version__,
            **(default_headers or {}),
        }

    def __repr__(self):
        return "%s(model=%r, base_url=%r)" % (type(self).__name__, self.model, self.base_url)

    # -- request building ----------------------------------------------------
    def _system_one_body(self, state: State, questions: Mapping[str, Any], model: Optional[str]) -> Dict[str, Any]:
        if not questions:
            raise ValueError("ask at least one question")
        return {"model": model or self.model, "state": state, "questions": dict(questions)}

    @staticmethod
    def _wire_questions(questions: Sequence[Type[Question]]) -> Dict[str, Dict[str, JSON]]:
        for q in questions:
            if not (isinstance(q, type) and issubclass(q, Question) and q.type):
                raise TypeError("expected a Choice / Score / Noul subclass, got %r" % (q,))
        names = [q.name for q in questions]
        if len(set(names)) != len(names):
            raise ValueError("duplicate question names: %r" % names)
        return {q.name: q.to_wire() for q in questions}

    @staticmethod
    def _ask_parser(questions: Sequence[Type[Question]]) -> Parse[Result]:
        return lambda payload, request_id: parse_result(questions, payload, request_id)

    # -- response handling ---------------------------------------------------
    @staticmethod
    def _json(response: httpx.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise JevResponseError("response is not JSON: %r" % response.text[:200], response.text) from exc

    @staticmethod
    def _error(response: httpx.Response):
        try:
            body = response.json()
        except ValueError:
            body = response.text
        return error_for_status(response.status_code, body, response.headers.get(REQUEST_ID_HEADER))

    def _should_retry(self, attempt: int, response: Optional[httpx.Response] = None) -> bool:
        if attempt >= self.max_retries:
            return False
        return response is None or response.status_code in RETRY_STATUSES

    @staticmethod
    def _delay(attempt: int, response: Optional[httpx.Response] = None) -> float:
        if response is not None:
            for header, scale in (("retry-after-ms", 1e-3), ("retry-after", 1.0)):
                try:
                    return min(max(float(response.headers[header]) * scale, 0.0), MAX_RETRY_AFTER)
                except (KeyError, ValueError):
                    continue
        # 0.5s doubling per attempt, capped at 8s, minus up to 25% jitter.
        return min(0.5 * 2 ** attempt, 8.0) * (1 - 0.25 * random.random())

    @staticmethod
    def _transport_error(exc: httpx.TransportError):
        if isinstance(exc, httpx.TimeoutException):
            return JevTimeoutError("request timed out: %s" % (exc or type(exc).__name__))
        return JevConnectionError("connection error: %s" % (exc or type(exc).__name__))

    # -- observation ---------------------------------------------------------
    def _finish(self, call: _Call, result: Any = None, error: Optional[BaseException] = None) -> None:
        """Report the call to every observer and stamp timing onto a Result."""
        event = call.event(error)
        if isinstance(result, Result):
            result.elapsed = event.duration
            result.attempts = event.attempts
        observers = [*self.observers, *scoped_observers()]
        if observers:
            emit(event, observers)


class Jev(_BaseClient):
    """Synchronous client. Thread-safe; reuse one instance (it pools connections)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        observe: Observe = None,
        http_client: Optional[httpx.Client] = None,
    ):
        super().__init__(api_key, model, base_url, timeout, max_retries, default_headers, observe)
        self._owns_http = http_client is None
        self._http = http_client or httpx.Client(timeout=timeout)

    def ask(self, state: State, *questions: Type[Question], model: Optional[str] = None) -> Result:
        """Answer every question about `state` in one request. Questions must have distinct names."""
        body = self._system_one_body(state, self._wire_questions(questions), model)
        return self._call("systemone", "POST", "/v1/systemone", body, self._ask_parser(questions))

    def system_one(self, state: State, questions: Mapping[str, Mapping[str, Any]], *, model: Optional[str] = None) -> Dict[str, Any]:
        """POST the raw Jev request shape; returns the response body unchanged.

            questions = {name: {"type": "choice"|"score"|"noul", "instructions": ..., "criteria": ...}}
        """
        body = self._system_one_body(state, questions, model)
        return self._call("systemone", "POST", "/v1/systemone", body, lambda payload, _: payload)

    def models(self) -> List[Model]:
        """The models and aliases this API key can use."""
        return self._call("models", "GET", "/v1/models", None, lambda payload, _: parse_models(payload))

    def _call(self, operation: str, method: str, path: str, body: Any, parse: Parse[T]) -> T:
        call = _Call(operation, method, self.base_url + path, body)
        try:
            call.payload = self._send(call, body)
            result = parse(call.payload, call.response.headers.get(REQUEST_ID_HEADER))
        except Exception as exc:
            self._finish(call, error=exc)
            raise
        self._finish(call, result)
        return result

    def _send(self, call: _Call, body: Any) -> Any:
        for attempt in range(self.max_retries + 1):
            call.attempt()
            try:
                response = self._http.request(call.method, call.url, json=body, headers=self._headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                call.attempted(None)
                if not self._should_retry(attempt):
                    raise self._transport_error(exc) from exc
                _sleep(self._delay(attempt))
                continue

            call.attempted(response)
            if response.is_success:
                return self._json(response)
            if not self._should_retry(attempt, response):
                raise self._error(response)
            _sleep(self._delay(attempt, response))
        raise AssertionError("unreachable")

    def close(self):
        if self._owns_http:
            self._http.close()

    def __enter__(self) -> "Jev":
        return self

    def __exit__(self, *exc):
        self.close()


class AsyncJev(_BaseClient):
    """asyncio client; same methods as `Jev`, awaited. Fan out with `asyncio.gather`."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        *,
        model: str = DEFAULT_MODEL,
        base_url: Optional[str] = None,
        timeout: Timeout = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        default_headers: Optional[Mapping[str, str]] = None,
        observe: Observe = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ):
        super().__init__(api_key, model, base_url, timeout, max_retries, default_headers, observe)
        self._owns_http = http_client is None
        self._http = http_client or httpx.AsyncClient(timeout=timeout)

    async def ask(self, state: State, *questions: Type[Question], model: Optional[str] = None) -> Result:
        """Answer every question about `state` in one request. Questions must have distinct names."""
        body = self._system_one_body(state, self._wire_questions(questions), model)
        return await self._call("systemone", "POST", "/v1/systemone", body, self._ask_parser(questions))

    async def system_one(self, state: State, questions: Mapping[str, Mapping[str, Any]], *, model: Optional[str] = None) -> Dict[str, Any]:
        """POST the raw Jev request shape; returns the response body unchanged."""
        body = self._system_one_body(state, questions, model)
        return await self._call("systemone", "POST", "/v1/systemone", body, lambda payload, _: payload)

    async def models(self) -> List[Model]:
        """The models and aliases this API key can use."""
        return await self._call("models", "GET", "/v1/models", None, lambda payload, _: parse_models(payload))

    async def _call(self, operation: str, method: str, path: str, body: Any, parse: Parse[T]) -> T:
        call = _Call(operation, method, self.base_url + path, body)
        try:
            call.payload = await self._send(call, body)
            result = parse(call.payload, call.response.headers.get(REQUEST_ID_HEADER))
        except Exception as exc:
            self._finish(call, error=exc)
            raise
        self._finish(call, result)
        return result

    async def _send(self, call: _Call, body: Any) -> Any:
        for attempt in range(self.max_retries + 1):
            call.attempt()
            try:
                response = await self._http.request(call.method, call.url, json=body, headers=self._headers, timeout=self.timeout)
            except httpx.TransportError as exc:
                call.attempted(None)
                if not self._should_retry(attempt):
                    raise self._transport_error(exc) from exc
                await _async_sleep(self._delay(attempt))
                continue

            call.attempted(response)
            if response.is_success:
                return self._json(response)
            if not self._should_retry(attempt, response):
                raise self._error(response)
            await _async_sleep(self._delay(attempt, response))
        raise AssertionError("unreachable")

    async def close(self):
        if self._owns_http:
            await self._http.aclose()

    async def __aenter__(self) -> "AsyncJev":
        return self

    async def __aexit__(self, *exc):
        await self.close()


def _int_or_none(value: Any) -> Optional[int]:
    try:
        return None if value is None else int(value)
    except (TypeError, ValueError):
        return None
