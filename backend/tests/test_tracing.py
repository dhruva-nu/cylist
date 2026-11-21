"""Tracing: off unless configured, and when on, a trace and a log line that
lead to each other.

The enabled tests hand the app a provider that exports to memory, so the spans
can be read back without a collector; everything else — the instrumentation,
the request log's tagging, the log formatter — is the code a deployment runs.
"""

from __future__ import annotations

import json
import threading
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import SpanKind, format_span_id, format_trace_id
from pydantic import ValidationError
from sqlalchemy import text

from app.config import Settings
from app.core.logging import REQUEST_ID_HEADER
from app.core.tracing import REQUEST_ID_ATTRIBUTE, Tracing, describe_tracing, traces_url
from app.db import Database
from app.main import create_app
from tests.conftest import as_new, client_for, client_on, sign_in

pytestmark = pytest.mark.usefixtures("restore_root_logger")
"""Every test here writes the log as JSON to a file of its own."""

EXPORT_THREAD = "OtelBatchSpanRecordProcessor"
"""The name the SDK's batch processor gives the thread it exports from."""


def json_settings(settings: Settings, log_file: Path, **update: Any) -> Settings:
    return settings.model_copy(
        update={"log_to_file": True, "log_file": log_file, "log_format": "json", **update}
    )


def access_lines(path: Path) -> list[dict[str, Any]]:
    """The request log's own lines — not httpx's, which the test client writes."""
    lines = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [line for line in lines if line["logger"] == "app.request"]


def export_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == EXPORT_THREAD]


# --- Off ---------------------------------------------------------------------


def test_there_is_no_endpoint_unless_one_is_given() -> None:
    assert Settings.model_fields["otlp_endpoint"].default == ""
    assert Settings.model_fields["trace_sample_ratio"].default == 1.0


async def test_an_app_without_an_endpoint_sets_up_no_tracing(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    configured = json_settings(settings, tmp_path / "off.log")
    assert Tracing.configured(configured) is None

    async with client_for(configured, database) as http:
        app: FastAPI = http.app  # type: ignore[attr-defined]
        assert not getattr(app, "_is_instrumented_by_opentelemetry", False)
        await http.get("/me")

    # Nothing was built: the global provider is still the API's placeholder,
    # and no export thread was started.
    assert isinstance(trace.get_tracer_provider(), trace.ProxyTracerProvider)
    assert export_threads() == []
    [line] = access_lines(tmp_path / "off.log")
    assert line["request_id"]
    assert "trace_id" not in line
    assert "span_id" not in line


# --- Configuration -----------------------------------------------------------


def test_the_endpoint_is_a_base_address_with_the_trace_path_appended() -> None:
    assert traces_url("http://alloy.monitoring:4318") == "http://alloy.monitoring:4318/v1/traces"
    assert traces_url("http://alloy.monitoring:4318/") == "http://alloy.monitoring:4318/v1/traces"
    assert (
        traces_url("http://alloy.monitoring:4318/v1/traces")
        == "http://alloy.monitoring:4318/v1/traces"
    )


def test_the_startup_line_names_the_receiver_without_its_credentials(
    settings: Settings,
) -> None:
    assert describe_tracing(settings) == "off"
    configured = settings.model_copy(
        update={
            "otlp_endpoint": "https://user:s3cret@otlp.example:4318/otlp",
            "trace_sample_ratio": 0.2,
        }
    )
    assert describe_tracing(configured) == "https://otlp.example:4318 at 0.2"


@pytest.mark.parametrize("endpoint", ["alloy.monitoring:4318", "grpc://alloy:4317", "http://"])
def test_an_endpoint_that_is_not_an_http_address_is_refused(endpoint: str) -> None:
    with pytest.raises(ValidationError, match="otlp_endpoint"):
        Settings(otlp_endpoint=endpoint)


@pytest.mark.parametrize("ratio", [-0.1, 1.5])
def test_a_sample_ratio_outside_zero_to_one_is_refused(ratio: float) -> None:
    with pytest.raises(ValidationError, match="trace_sample_ratio"):
        Settings(trace_sample_ratio=ratio)


def test_a_configured_provider_names_the_service_its_environment_and_its_ratio(
    settings: Settings,
) -> None:
    tracing = Tracing.configured(
        settings.model_copy(
            update={
                "otlp_endpoint": "http://alloy.monitoring:4318",
                "trace_sample_ratio": 0.2,
                "environment": "prod",
            }
        )
    )
    assert tracing is not None
    try:
        assert len(export_threads()) == 1
        attributes = tracing.provider.resource.attributes
        assert attributes["service.name"] == "cylist"
        assert attributes["deployment.environment"] == "prod"
        description = tracing.provider.sampler.get_description()
        assert description.startswith("ParentBased")
        assert "TraceIdRatioBased{0.2}" in description
    finally:
        tracing.shutdown()
    assert export_threads() == []


# --- On ----------------------------------------------------------------------


@dataclass
class Traced:
    http: httpx.AsyncClient
    spans: InMemorySpanExporter
    log: Path
    database: Database

    def finished(self) -> tuple[ReadableSpan, ...]:
        return self.spans.get_finished_spans()

    def servers(self) -> list[ReadableSpan]:
        return [span for span in self.finished() if span.kind is SpanKind.SERVER]


@pytest.fixture
async def traced(settings: Settings, tmp_path: Path) -> AsyncIterator[Traced]:
    """An app tracing into memory, on a pool of its own that is traced too.

    Its own pool rather than the session's, because instrumenting an engine is
    for the engine's life: the shared one would go on tracing every later
    test's statements into a provider that had been shut down.
    """
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    tracing = Tracing(provider)

    configured = json_settings(settings, tmp_path / "traced.log")
    database = Database.configured(configured)
    tracing.instrument_engine(database.engine)
    app = create_app(configured, tracing=tracing)
    try:
        async with client_on(as_new(app, configured, database)) as http:
            yield Traced(http, spans, tmp_path / "traced.log", database)
    finally:
        # The instrumentation patches Starlette's BackgroundTask for the
        # process; this puts it back.
        FastAPIInstrumentor.uninstrument_app(app)
        await database.dispose()
        tracing.shutdown()


async def test_a_request_is_a_server_span_carrying_its_request_id(traced: Traced) -> None:
    await sign_in(traced.http)
    traced.spans.clear()

    response = await traced.http.get("/me")

    assert response.status_code == 200
    [server] = traced.servers()
    assert server.attributes is not None
    assert server.attributes[REQUEST_ID_ATTRIBUTE] == response.headers[REQUEST_ID_HEADER]
    assert server.parent is None


async def test_the_statements_a_request_sends_are_its_children(traced: Traced) -> None:
    await sign_in(traced.http)
    traced.spans.clear()

    await traced.http.get("/me")

    [server] = traced.servers()
    statements = [span for span in traced.finished() if span.kind is SpanKind.CLIENT]
    assert statements, "the request read the session and the person from Postgres"
    for statement in statements:
        assert statement.parent is not None
        assert statement.context is not None and server.context is not None
        assert statement.context.trace_id == server.context.trace_id
        assert statement.attributes is not None
        assert statement.attributes.get("db.system") == "postgresql"


async def test_a_statement_outside_any_request_is_not_traced(traced: Traced) -> None:
    """The courier and the reaper query on a timer; a trace each would be noise."""
    async with traced.database.session() as session:
        await session.execute(text("SELECT 1"))

    assert traced.finished() == ()


async def test_a_log_line_carries_the_trace_it_was_written_in(traced: Traced) -> None:
    await sign_in(traced.http)
    traced.spans.clear()

    response = await traced.http.get("/me")

    [server] = traced.servers()
    assert server.context is not None
    request_id = response.headers[REQUEST_ID_HEADER]
    [line] = [line for line in access_lines(traced.log) if line["request_id"] == request_id]
    assert line["trace_id"] == format_trace_id(server.context.trace_id)
    assert line["span_id"] == format_span_id(server.context.span_id)


async def test_health_checks_are_not_traced(traced: Traced) -> None:
    assert (await traced.http.get("/health")).status_code == 200
    assert (await traced.http.get("/health/requests")).status_code == 200

    assert traced.servers() == []


async def test_a_callers_trace_context_is_not_adopted(traced: Traced) -> None:
    """A stranger's sampled flag and trace id would otherwise decide ours."""
    foreign_trace = "4bf92f3577b34da6a3ce929d0e0e4736"
    await traced.http.get("/me", headers={"traceparent": f"00-{foreign_trace}-00f067aa0ba902b7-01"})

    [server] = traced.servers()
    assert server.parent is None
    assert server.context is not None
    assert format_trace_id(server.context.trace_id) != foreign_trace


async def test_an_instrumented_client_sends_each_request_as_a_span() -> None:
    """What the hook courier's client is given when tracing is on."""
    spans = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(spans))
    tracing = Tracing(provider)
    try:
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda _: httpx.Response(204))
        ) as client:
            tracing.instrument_client(client)
            await client.post("https://receiver.example/hook", json={})
    finally:
        tracing.shutdown()

    [span] = spans.get_finished_spans()
    assert span.kind is SpanKind.CLIENT
    assert span.attributes is not None
    assert (
        span.attributes.get("http.response.status_code", span.attributes.get("http.status_code"))
        == 204
    )
