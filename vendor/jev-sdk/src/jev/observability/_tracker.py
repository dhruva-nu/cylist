"""`UsageTracker`: running totals of calls, time and tokens."""
from __future__ import annotations

import threading
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Deque, Dict, List, NamedTuple

from ._core import TIME, TOKENS, Metrics, RequestEvent, format_duration, parse_metrics


class _Row(NamedTuple):
    """What a summary needs from one event; kept for every call, so it stays small."""

    duration: float
    ok: bool
    attempts: int
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class UsageSummary:
    requests: int
    errors: int
    attempts: int  # > requests means retries happened
    input_tokens: int
    output_tokens: int
    total_time: float  # seconds, summed over calls (concurrent calls overlap)
    mean_time: float
    p50_time: float
    p95_time: float
    max_time: float

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> Dict[str, Any]:
        return {**asdict(self), "total_tokens": self.total_tokens}


class UsageTracker:
    """Accumulates every event it is given. Thread-safe; share one across clients.

        usage = UsageTracker()
        jev = Jev(observe=usage)
        ...
        usage.input_tokens, usage.total_time, usage.summary()
        usage.by_model()["jev-1.13.0"].p95_time
    """

    def __init__(self, keep_events: int = 1000):
        self._lock = threading.Lock()
        self._events: Deque[RequestEvent] = deque(maxlen=keep_events)
        self._rows: List[_Row] = []
        self._by_model: Dict[str, List[_Row]] = {}

    def __call__(self, event: RequestEvent) -> None:
        row = _Row(event.duration, event.ok, event.attempts, event.input_tokens or 0, event.output_tokens or 0)
        with self._lock:
            self._events.append(event)
            self._rows.append(row)
            self._by_model.setdefault(event.response_model or event.model or "-", []).append(row)

    def reset(self) -> None:
        with self._lock:
            self._events.clear()
            self._rows.clear()
            self._by_model.clear()

    # -- totals --------------------------------------------------------------
    @property
    def events(self) -> List[RequestEvent]:
        """The most recent events (up to `keep_events`), oldest first."""
        with self._lock:
            return list(self._events)

    @property
    def requests(self) -> int:
        return self.snapshot().requests

    @property
    def errors(self) -> int:
        return self.snapshot().errors

    @property
    def input_tokens(self) -> int:
        return self.snapshot().input_tokens

    @property
    def output_tokens(self) -> int:
        return self.snapshot().output_tokens

    @property
    def total_tokens(self) -> int:
        return self.snapshot().total_tokens

    @property
    def total_time(self) -> float:
        return self.snapshot().total_time

    def snapshot(self) -> UsageSummary:
        with self._lock:
            return _summarize(self._rows)

    def by_model(self) -> Dict[str, UsageSummary]:
        with self._lock:
            return {model: _summarize(rows) for model, rows in self._by_model.items()}

    def summary(self, metrics: Metrics = True) -> str:
        """A short human-readable report of the chosen metrics."""
        chosen = parse_metrics(metrics)
        s = self.snapshot()
        lines = ["jev usage: %d request%s%s%s" % (
            s.requests, "" if s.requests == 1 else "s",
            " (%d failed)" % s.errors if s.errors else "",
            ", %d retries" % (s.attempts - s.requests) if s.attempts > s.requests else "",
        )]
        if s.requests and TIME in chosen:
            lines.append("  time    total %s  mean %s  p50 %s  p95 %s  max %s" % tuple(
                format_duration(v) for v in (s.total_time, s.mean_time, s.p50_time, s.p95_time, s.max_time)))
        if s.requests and TOKENS in chosen:
            ok = s.requests - s.errors
            per = " (%.0f in / request)" % (s.input_tokens / ok) if ok else ""
            lines.append("  tokens  in %s  out %s  total %s%s" % (
                format(s.input_tokens, ","), format(s.output_tokens, ","), format(s.total_tokens, ","), per))
        return "\n".join(lines)

    def __repr__(self):
        s = self.snapshot()
        return "UsageTracker(requests=%d, errors=%d, total_tokens=%d, total_time=%.3fs)" % (
            s.requests, s.errors, s.total_tokens, s.total_time)


def _summarize(rows: List[_Row]) -> UsageSummary:
    times = sorted(r.duration for r in rows)
    n = len(times)
    return UsageSummary(
        requests=n,
        errors=sum(1 for r in rows if not r.ok),
        attempts=sum(r.attempts for r in rows),
        input_tokens=sum(r.input_tokens for r in rows),
        output_tokens=sum(r.output_tokens for r in rows),
        total_time=sum(times),
        mean_time=sum(times) / n if n else 0.0,
        p50_time=_percentile(times, 0.50),
        p95_time=_percentile(times, 0.95),
        max_time=times[-1] if n else 0.0,
    )


def _percentile(sorted_values: List[float], q: float) -> float:
    """Linear-interpolated percentile of already-sorted values; 0.0 when empty."""
    if not sorted_values:
        return 0.0
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)
