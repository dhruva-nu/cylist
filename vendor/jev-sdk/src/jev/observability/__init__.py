"""See how long Jev calls take and how many tokens they use.

Every call a client makes, retries and failures included, becomes a `RequestEvent` (duration,
latency, attempts, input/output tokens, model, request id, error). Observers receive them.

Turn it on per client:

    Jev(observe="time")        # print one line per call: duration only
    Jev(observe="tokens")      # ... tokens only
    Jev(observe="all")         # ... all of them   (also: observe=True)
    export JEV_OBSERVE=all     # same, with no code change (clients built with observe=None)

or for a block of code, across every client and asyncio task inside it:

    with track() as usage:                 # silent; totals only
        jev.ask(...)
    print(usage.summary())                 # requests, time (mean/p50/p95), tokens

    with track("tokens"):                  # also print one line per call
        ...

or with any observer (a callable taking a RequestEvent), alone or several:

    usage = UsageTracker()
    Jev(observe=[usage, LogObserver("all"), OpenTelemetryObserver()])

Every Result also carries `result.elapsed` and `result.attempts`, observed or not.
"""
from ._console import ConsoleObserver, LogObserver
from ._core import (
    ENV_VAR,
    Metrics,
    Observe,
    Observer,
    RequestEvent,
    format_event,
    parse_metrics,
    track,
)
from ._tracker import UsageSummary, UsageTracker

__all__ = [
    "RequestEvent", "Observer", "Observe", "Metrics", "ENV_VAR",
    "track", "format_event", "parse_metrics",
    "UsageTracker", "UsageSummary", "ConsoleObserver", "LogObserver", "OpenTelemetryObserver",
]


def __getattr__(name):
    # Lazy, so importing jev never requires opentelemetry.
    if name == "OpenTelemetryObserver":
        from ._otel import OpenTelemetryObserver

        return OpenTelemetryObserver
    raise AttributeError("module %r has no attribute %r" % (__name__, name))
