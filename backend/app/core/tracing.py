"""Traces: where a request's time went, linked to the log lines it wrote.

The log says *what* happened to a request; a trace says *where its time went*
— the server span for the request, a child span for every statement it sent
Postgres, and one for every hook the courier POSTed. The two are joined both
ways: every JSON log line written inside a sampled span carries its
``trace_id`` and ``span_id`` (see :mod:`app.core.logging`), and every server
span carries the request id as ``cylist.request_id``, which is what a person
holding an ``X-Request-ID`` from a bug report has to search by.

Off unless asked for
--------------------

Nothing here runs unless ``CYLIST_OTLP_ENDPOINT`` is set. Unset — the default,
and what the tests and everyone's own machine have — :meth:`Tracing.configured`
returns ``None``: the SDK is never imported, no provider is built and no
exporter thread is started, and the only cost left on a request is asking the
OpenTelemetry API for the current span and being told there is none.

Set, spans are batched and sent as OTLP over HTTP/protobuf to
``<endpoint>/v1/traces`` — ``http://alloy.monitoring:4318``, say — and the
share of requests traced is ``CYLIST_TRACE_SAMPLE_RATIO``: all of them by
default, a fifth in production, where the volume is.

Trace context from outside is not believed
------------------------------------------

The sampler is ``ParentBased(TraceIdRatioBased)``, which samples a root span
by its trace id and then follows its parent's decision — so a request's
statements are traced exactly when the request is. A *remote* parent is the
problem: Cylist is the first hop, answering the public internet, and a
``traceparent`` header with the sampled flag set would otherwise make the
caller's choice of trace id, and whether it is sampled, ours. Anyone could
trace every one of their requests in production at a ratio of 0.2. So the
trace-context headers are dropped before the instrumentation reads them, and
every request starts a trace of its own — see :class:`_DropInboundTraceContext`.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from opentelemetry import trace
from opentelemetry.metrics import NoOpMeterProvider
from opentelemetry.trace import format_span_id, format_trace_id
from starlette.types import ASGIApp, Receive, Scope, Send

if TYPE_CHECKING:  # pragma: no cover - only for types; the SDK is imported when enabled
    import httpx
    from fastapi import FastAPI
    from opentelemetry.context import Context
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.util.types import Attributes
    from sqlalchemy.ext.asyncio import AsyncEngine

    from app.config import Settings

SERVICE_NAME: Final = "cylist"

REQUEST_ID_ATTRIBUTE: Final = "cylist.request_id"
"""The server span's attribute holding the request id — the one
``X-Request-ID`` and every log line of the request carry."""

TRACES_PATH: Final = "/v1/traces"
"""Where an OTLP/HTTP receiver takes spans, under its base address."""

UNTRACED_URLS: Final = "/api/v1/health"
"""Searched for in each request's URL; a match is not traced at all.

``/api/v1/health`` and ``/api/v1/health/requests`` — the healthcheck every
thirty seconds and whatever is scraping the counters. A trace of a request that
always succeeds in a millisecond answers no question, and at 100% sampling on
staging it would be most of the traces there are. Searched rather than matched,
so a dev slot's ``/dev_1/api/v1/health`` is as quiet as production's.
"""

_TRACE_CONTEXT_HEADERS: Final = frozenset({b"traceparent", b"tracestate", b"baggage"})

_NO_METRICS: Final = NoOpMeterProvider()
"""Handed to every instrumentation, which would otherwise record metrics of its
own on the global meter. Cylist's metrics are Prometheus's to serve; this
module is traces only."""


def traces_url(endpoint: str) -> str:
    """The URL spans are POSTed to, from ``CYLIST_OTLP_ENDPOINT``.

    The setting is the receiver's base address, as ``OTEL_EXPORTER_OTLP_ENDPOINT``
    is, and the trace path is appended — unless it is already there, since a
    full URL is the other thing people paste.
    """
    base = endpoint.rstrip("/")
    return base if base.endswith(TRACES_PATH) else base + TRACES_PATH


def describe_tracing(settings: Settings) -> str:
    """Whether traces are sent, and where, for the startup line.

    The receiver's scheme, host and port only: anything else in the address —
    a ``user:token@`` most of all — is not the log's to repeat.
    """
    if not settings.otlp_endpoint:
        return "off"
    parts = urlsplit(settings.otlp_endpoint)
    host = parts.hostname or "?"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port} at {settings.trace_sample_ratio:g}"


class Tracing:
    """One tracer provider, and the instrumentation that reports to it.

    Built by :meth:`configured` from the settings, or around a provider a test
    built with an in-memory exporter. Every instrumentation is handed this
    provider explicitly and the global one is never set, so two apps in one
    process — which is what a test run is — cannot report into each other's.
    """

    def __init__(self, provider: TracerProvider) -> None:
        self._provider = provider

    @classmethod
    def configured(cls, settings: Settings) -> Tracing | None:
        """Tracing as these settings describe it, or ``None`` when it is off.

        The SDK, the exporter and the instrumentations are imported where they
        are used rather than at the top of the module, so a deployment with
        tracing off never loads any of them — only the API, which is what the
        log formatter asks for the current span. The batch processor starts
        its export thread as it is built, which is why nothing is built when
        there is no endpoint.
        """
        if not settings.otlp_endpoint:
            return None

        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
        from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

        provider = TracerProvider(
            resource=Resource.create(
                {
                    "service.name": SERVICE_NAME,
                    # Both spellings: the older is what most dashboards still
                    # filter on, the newer is the stable convention.
                    "deployment.environment": settings.environment,
                    "deployment.environment.name": settings.environment,
                }
            ),
            sampler=ParentBased(TraceIdRatioBased(settings.trace_sample_ratio)),
        )
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(
                    endpoint=traces_url(settings.otlp_endpoint),
                    meter_provider=_NO_METRICS,
                )
            )
        )
        return cls(provider)

    @property
    def provider(self) -> TracerProvider:
        return self._provider

    def instrument_app(self, app: FastAPI) -> None:
        """Give every request but a health check a server span.

        The instrumentation puts its middleware outside the whole stack, so
        the span covers the base path, the security headers and the request
        log as well as the route — and is current by the time the request log
        binds the request id, which is where :func:`tag_request` is called.
        The per-message ``receive``/``send`` spans it would also make are left
        out: one per chunk of a body says nothing the server span does not.
        """
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            tracer_provider=self._provider,
            meter_provider=_NO_METRICS,
            excluded_urls=UNTRACED_URLS,
            exclude_spans=["receive", "send"],
        )
        # Outside even the instrumentation's own middleware, which is the only
        # place left that runs before it reads the headers.
        traced = app.build_middleware_stack

        def build_middleware_stack() -> ASGIApp:
            return _DropInboundTraceContext(traced())

        app.build_middleware_stack = build_middleware_stack  # type: ignore[method-assign]

    def instrument_engine(self, engine: AsyncEngine) -> None:
        """A child span for every statement sent on this engine's connections.

        This engine only — the instrumentation's own ``instrument()`` would
        patch ``create_engine`` for the whole process. The listeners go when
        the engine does.

        A *child* span only: a statement sent with no span current — the hook
        courier's pass every fifteen seconds, the reaper's every minute — is
        not traced at all, rather than becoming a trace of one span each, which
        would outnumber the requests on a quiet deployment.

        Statements are recorded as SQLAlchemy sends them, with bound parameters
        as placeholders, so no value a row holds — a vault secret, a password
        hash — ever reaches a span.
        """
        from opentelemetry.instrumentation.sqlalchemy.engine import EngineTracer

        tracer = _ChildrenOnly(trace.get_tracer(__name__, tracer_provider=self._provider))
        connections = _NO_METRICS.get_meter(__name__).create_up_down_counter(
            "db.client.connections.usage"
        )
        EngineTracer(tracer, engine.sync_engine, connections)

    def instrument_client(self, client: httpx.AsyncClient) -> None:
        """A span for every request this client sends — the hook courier's."""
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor.instrument_client(
            client, tracer_provider=self._provider, meter_provider=_NO_METRICS
        )

    def shutdown(self) -> None:
        """Send what is still batched, then stop the export thread.

        Bounded by the exporter's own timeout, so a receiver that has gone
        away delays a shutdown by seconds rather than holding it forever.
        """
        self._provider.shutdown()


class _ChildrenOnly(trace.NoOpTracer):
    """A tracer that starts a span only under one that is already current.

    Everything else it answers as the no-op tracer does — the invalid span —
    which the instrumentation records nothing on and ends without a trace.
    Subclassing the no-op tracer is what gives ``start_as_current_span`` for
    free: it opens whatever :meth:`start_span` returns.
    """

    def __init__(self, tracer: trace.Tracer) -> None:
        self._tracer = tracer

    def start_span(
        self,
        name: str,
        context: Context | None = None,
        kind: trace.SpanKind = trace.SpanKind.INTERNAL,
        attributes: Attributes = None,
        links: Sequence[trace.Link] | None = None,
        start_time: int | None = None,
        record_exception: bool = True,
        set_status_on_exception: bool = True,
    ) -> trace.Span:
        if not trace.get_current_span(context).get_span_context().is_valid:
            return trace.INVALID_SPAN
        return self._tracer.start_span(
            name,
            context,
            kind,
            attributes,
            links,
            start_time,
            record_exception,
            set_status_on_exception,
        )


class _DropInboundTraceContext:
    """Remove the W3C trace-context headers from every request.

    For the reason the module gives: a trace begins here, whatever the caller
    says. The scope is copied rather than edited, since it is not this
    middleware's to change for anything outside it.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] in ("http", "websocket"):
            headers = scope.get("headers", [])
            if any(name in _TRACE_CONTEXT_HEADERS for name, _value in headers):
                scope = {
                    **scope,
                    "headers": [
                        (name, value)
                        for name, value in headers
                        if name not in _TRACE_CONTEXT_HEADERS
                    ],
                }
        await self.app(scope, receive, send)


def tag_request(request_id: str) -> None:
    """Put the request id on the span serving this request, if one is.

    Called by :class:`app.core.request_log.RequestLogMiddleware` as it binds
    the id. With tracing off, or for a request the sampler passed over, the
    current span is not recording and this does nothing.
    """
    span = trace.get_current_span()
    if span.is_recording():
        span.set_attribute(REQUEST_ID_ATTRIBUTE, request_id)


def current_trace_ids() -> tuple[str, str] | None:
    """The ``(trace_id, span_id)`` of the current span, as hex, if it was sampled.

    Sampled, not merely valid: with a ratio below one, a request the sampler
    passed over still has a well-formed trace id, but no trace was ever sent
    under it. A log line carrying it would link to nothing, and teach whoever
    clicked it that the links cannot be trusted — the request id already ties
    that request's lines together.
    """
    context = trace.get_current_span().get_span_context()
    if not context.is_valid or not context.trace_flags.sampled:
        return None
    return format_trace_id(context.trace_id), format_span_id(context.span_id)
