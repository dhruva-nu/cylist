"""The request event every observer receives, metric selection, formatting and `track()`."""
from __future__ import annotations

import contextvars
import logging
import os
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable, FrozenSet, Iterable, Iterator, List, Optional, Sequence, Tuple, Union

if TYPE_CHECKING:
    from ._tracker import UsageTracker

log = logging.getLogger("jev")

TIME = "time"
TOKENS = "tokens"
ALL_METRICS: FrozenSet[str] = frozenset({TIME, TOKENS})

# "time" | "tokens" | "all" | "time,tokens" | True | an iterable of names
Metrics = Union[str, bool, Iterable[str]]


@dataclass(frozen=True)
class RequestEvent:
    """One logical API call as the client saw it, retries included. Emitted on success and failure."""

    operation: str  # "systemone" | "models"
    method: str
    url: str
    model: Optional[str]  # requested model or alias ("jev-latest"); None for /v1/models
    response_model: Optional[str]  # the model that answered ("jev-1.13.0")
    questions: Tuple[str, ...]  # question names sent, in order
    status_code: Optional[int]  # of the final attempt; None if no response arrived
    request_id: Optional[str]
    attempts: int  # 1 + retries
    started_at: float  # unix time the call began
    duration: float  # seconds, wall clock for the whole call incl. retries and backoff
    latency: Optional[float]  # seconds for the final attempt alone (roughly the API's own time)
    input_tokens: Optional[int]  # None when the call failed or the endpoint reports none
    output_tokens: Optional[int]
    error: Optional[BaseException] = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def total_tokens(self) -> Optional[int]:
        if self.input_tokens is None and self.output_tokens is None:
            return None
        return (self.input_tokens or 0) + (self.output_tokens or 0)

    @property
    def duration_ms(self) -> float:
        return self.duration * 1000


# An observer is any callable taking a RequestEvent; the built-ins are classes with __call__.
Observer = Callable[[RequestEvent], None]


# ----------------------------------------------------------------------------- metric selection
def parse_metrics(metrics: Metrics) -> FrozenSet[str]:
    """{"time"}, {"tokens"} or all of them, from the forms users write."""
    if metrics is True:
        return ALL_METRICS
    if isinstance(metrics, str):
        names = [m.strip().lower() for m in metrics.replace("+", ",").split(",") if m.strip()]
    else:
        names = [str(m).lower() for m in metrics]
    chosen = set()
    for name in names:
        if name in ("all", "1", "true", "yes", "on"):
            chosen |= ALL_METRICS
        elif name in ("time", "timing", "latency"):
            chosen.add(TIME)
        elif name in ("tokens", "token", "usage"):
            chosen.add(TOKENS)
        else:
            raise ValueError("unknown metric %r: use 'time', 'tokens' or 'all'" % name)
    if not chosen:
        raise ValueError("no metrics selected: use 'time', 'tokens' or 'all'")
    return frozenset(chosen)


# ----------------------------------------------------------------------------- formatting
def format_event(event: RequestEvent, metrics: Metrics = True) -> str:
    """One line, e.g. `jev systemone jev-1.13.0 3q 200 | 412ms | tokens in=375 out=62 | req_01..`."""
    chosen = parse_metrics(metrics)
    head = ["jev", event.operation]
    if event.response_model or event.model:
        head.append(event.response_model or event.model)
    if event.questions:
        head.append("%dq" % len(event.questions))
    head.append(str(event.status_code) if event.status_code is not None else "no-response")
    parts = [" ".join(head)]

    if TIME in chosen:
        t = format_duration(event.duration)
        if event.attempts > 1:
            t += " (%d attempts, last %s)" % (event.attempts, format_duration(event.latency or 0.0))
        parts.append(t)
    if TOKENS in chosen:
        if event.input_tokens is None and event.output_tokens is None:
            parts.append("tokens -")
        else:
            parts.append("tokens in=%d out=%d" % (event.input_tokens or 0, event.output_tokens or 0))
    if event.error is not None:
        parts.append("error %s: %s" % (type(event.error).__name__, event.error))
    if event.request_id:
        parts.append(event.request_id)
    return " | ".join(parts)


def format_duration(seconds: float) -> str:
    return "%.0fms" % (seconds * 1000) if seconds < 10 else "%.2fs" % seconds


# ----------------------------------------------------------------------------- dispatch
# Observers attached by `track()` blocks around the current code (and the tasks it spawns).
_scoped: contextvars.ContextVar[Tuple[Observer, ...]] = contextvars.ContextVar("jev_observers", default=())


def scoped_observers() -> Tuple[Observer, ...]:
    return _scoped.get()


def emit(event: RequestEvent, observers: Iterable[Observer]) -> None:
    """Hand `event` to every observer. A failing observer is logged, never raised: it must not break the call."""
    for observer in observers:
        try:
            observer(event)
        except Exception:  # noqa: BLE001
            log.exception("jev observer %r failed", observer)


@contextmanager
def track(metrics: Optional[Metrics] = None, *observers: Observer) -> Iterator["UsageTracker"]:
    """Observe every Jev call made inside the block, from any client, and total them up.

        with track() as usage:
            jev.ask(...); jev.ask(...)
        print(usage.summary())           # requests, time, tokens

        with track("time"):              # also print one line per call, time only
            ...

    Extra observers (e.g. a LogObserver) receive the block's events too. Blocks nest, and
    asyncio tasks created inside the block report to it.
    """
    from ._console import ConsoleObserver
    from ._tracker import UsageTracker

    tracker = UsageTracker()
    extra: List[Observer] = [tracker, *observers]
    if metrics is not None and metrics is not False:
        extra.append(ConsoleObserver(metrics))
    token = _scoped.set(_scoped.get() + tuple(extra))
    try:
        yield tracker
    finally:
        _scoped.reset(token)


# ----------------------------------------------------------------------------- client option
# What a client's `observe=` accepts.
Observe = Union[None, bool, str, Observer, Sequence[Union[str, Observer]]]
ENV_VAR = "JEV_OBSERVE"


def resolve_observers(observe: Observe) -> List[Observer]:
    """Turn a client's `observe=` argument into observers.

    None -> $JEV_OBSERVE if set, else nothing;  False -> nothing (ignores the env var);
    True / "time" / "tokens" / "all" -> a ConsoleObserver printing those metrics;
    a callable -> itself;  a list -> each of the above.
    """
    from ._console import ConsoleObserver

    if observe is None:
        env = os.environ.get(ENV_VAR, "").strip()
        if not env or env.lower() in ("0", "false", "no", "off"):
            return []
        return [ConsoleObserver(env)]
    if observe is False:
        return []
    if observe is True or isinstance(observe, str):
        return [ConsoleObserver(observe)]
    if callable(observe):
        return [observe]
    resolved: List[Observer] = []
    for item in observe:
        resolved += resolve_observers(item)
    return resolved
