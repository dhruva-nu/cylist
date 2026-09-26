"""OpenTelemetry export: a span per call plus duration and token histograms.

Needs `opentelemetry-api` (`pip install "jev[otel]"`); providers default to the global ones,
so whatever exporter the application configured (OTLP, console, ...) receives the data.

Attribute names follow the OpenTelemetry GenAI semantic conventions where they apply.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from ._core import RequestEvent

SYSTEM = "typesafe"


class OpenTelemetryObserver:
    """Record each call as a `jev.<operation>` span and as GenAI client metrics.

        from opentelemetry import trace, metrics
        Jev(observe=OpenTelemetryObserver())

    spans    jev.systemone / jev.models, timed from the call's real start to end
    metrics  gen_ai.client.operation.duration  (histogram, s)
             gen_ai.client.token.usage         (histogram, {token}; gen_ai.token.type=input|output)
    """

    def __init__(self, tracer_provider: Any = None, meter_provider: Any = None, *, spans: bool = True, metrics: bool = True):
        try:
            from opentelemetry import metrics as otel_metrics
            from opentelemetry import trace
        except ImportError as exc:  # pragma: no cover - exercised only without the extra
            raise ImportError('OpenTelemetryObserver needs opentelemetry-api: pip install "jev[otel]"') from exc

        from .._version import __version__

        self._trace = trace
        self._tracer = trace.get_tracer("jev", __version__, tracer_provider=tracer_provider) if spans else None
        self._duration = self._tokens = None
        if metrics:
            meter = otel_metrics.get_meter("jev", __version__, meter_provider=meter_provider)
            self._duration = meter.create_histogram(
                "gen_ai.client.operation.duration", unit="s", description="Duration of Jev API calls, retries included"
            )
            self._tokens = meter.create_histogram(
                "gen_ai.client.token.usage", unit="{token}", description="Tokens used per Jev API call"
            )

    def __call__(self, event: RequestEvent) -> None:
        attrs = _attributes(event)
        if self._tracer is not None:
            self._span(event, attrs)
        if self._duration is not None:
            common = {k: v for k, v in attrs.items() if k in _METRIC_KEYS}
            self._duration.record(event.duration, common)
            if event.input_tokens is not None:
                self._tokens.record(event.input_tokens, {**common, "gen_ai.token.type": "input"})
            if event.output_tokens is not None:
                self._tokens.record(event.output_tokens, {**common, "gen_ai.token.type": "output"})

    def _span(self, event: RequestEvent, attrs: Dict[str, Any]) -> None:
        start = int(event.started_at * 1e9)
        span = self._tracer.start_span("jev.%s" % event.operation, kind=self._trace.SpanKind.CLIENT, start_time=start, attributes=attrs)
        if event.error is not None:
            span.record_exception(event.error)
            span.set_status(self._trace.Status(self._trace.StatusCode.ERROR, str(event.error)))
        span.end(end_time=start + int(event.duration * 1e9))


_METRIC_KEYS = {
    "gen_ai.system", "gen_ai.operation.name", "gen_ai.request.model", "gen_ai.response.model",
    "http.response.status_code", "error.type",
}


def _attributes(event: RequestEvent) -> Dict[str, Any]:
    attrs: Dict[str, Any] = {
        "gen_ai.system": SYSTEM,
        "gen_ai.operation.name": event.operation,
        "http.request.method": event.method,
        "url.full": event.url,
        "jev.attempts": event.attempts,
    }
    optional: Dict[str, Optional[Any]] = {
        "gen_ai.request.model": event.model,
        "gen_ai.response.model": event.response_model,
        "gen_ai.usage.input_tokens": event.input_tokens,
        "gen_ai.usage.output_tokens": event.output_tokens,
        "http.response.status_code": event.status_code,
        "jev.request_id": event.request_id,
        "jev.questions": list(event.questions) or None,
        "jev.latency": event.latency,
        "error.type": type(event.error).__name__ if event.error is not None else None,
    }
    attrs.update({k: v for k, v in optional.items() if v is not None})
    return attrs
