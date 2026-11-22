"""Prometheus metrics, and the port they are kept on (CYLIST-195)."""

from __future__ import annotations

import asyncio
import socket
import time
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from prometheus_client.parser import text_string_to_metric_families
from sqlalchemy import select

from app.config import Settings
from app.core.prometheus import Prometheus, PrometheusMiddleware
from app.db import Database
from app.main import create_app
from tests.conftest import as_new, client_on


def scrape(app: FastAPI) -> dict[tuple[str, frozenset[tuple[str, str]]], float]:
    """Every sample the app's registry would serve, by name and labels."""
    prometheus: Prometheus = app.state.prometheus
    return {
        (sample.name, frozenset(sample.labels.items())): sample.value
        for family in text_string_to_metric_families(prometheus.exposition().decode())
        for sample in family.samples
    }


def sample(app: FastAPI, name: str, **labels: str) -> float | None:
    return scrape(app).get((name, frozenset(labels.items())))


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def metrics_on(settings: Settings, **more: object) -> Settings:
    return settings.model_copy(
        update={"metrics_enabled": True, "metrics_host": "127.0.0.1", **more}
    )


async def settled(app: FastAPI) -> None:
    """Wait until a running app's startup work has quietened down.

    The event bus's line is up, and the pool has opened a connection and has
    it back. Ending a lifespan earlier can cancel the hook courier's first
    pass while SQLAlchemy is still setting up a new connection, which it then
    never closes — and the next test is the one that trips over the
    ResourceWarning when it is collected. Later passes reuse the pooled
    connection, so past this point shutting down is clean.
    """
    for _ in range(500):
        pool = app.state.database.pool_status()
        if app.state.bus.listening.is_set() and pool.waits >= 1 and pool.in_use == 0:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("the app did not settle")


@pytest.fixture
async def counted(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    """A client on an app built with metrics on, without its lifespan."""
    configured = metrics_on(settings)
    async with client_on(as_new(create_app(configured), configured, database)) as http:
        yield http


def app_of(client: AsyncClient) -> FastAPI:
    app: FastAPI = client.app  # type: ignore[attr-defined]
    return app


class TestOffByDefault:
    def test_the_settings_default_to_off_on_9090(self) -> None:
        read = Settings(_env_file=None)
        assert read.metrics_enabled is False
        assert read.metrics_port == 9090

    def test_reads_the_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CYLIST_METRICS_ENABLED", "true")
        monkeypatch.setenv("CYLIST_METRICS_PORT", "9191")
        read = Settings(_env_file=None)
        assert read.metrics_enabled is True
        assert read.metrics_port == 9191

    def test_nothing_is_counted(self, app: FastAPI) -> None:
        assert app.state.prometheus is None
        installed: list[object] = [m.cls for m in app.user_middleware]
        assert PrometheusMiddleware not in installed

    async def test_no_listener_is_started(self, settings: Settings) -> None:
        port = free_port()
        configured = settings.model_copy(update={"metrics_host": "127.0.0.1", "metrics_port": port})
        app = create_app(configured)
        async with app.router.lifespan_context(app):
            await settled(app)
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=1).close()


class TestRequests:
    async def test_are_labelled_by_route_template(self, counted: AsyncClient) -> None:
        await counted.get("/health")
        await counted.get("/projects/ATL/summary")
        await counted.get("/projects/ZZZ/summary")

        app = app_of(counted)
        assert sample(
            app, "http_requests_total", method="GET", route="/api/v1/health", status="200"
        )
        assert (
            sample(
                app,
                "http_requests_total",
                method="GET",
                route="/api/v1/projects/{project_ref}/summary",
                status="401",
            )
            == 2
        )
        routes = {dict(labels).get("route") for _, labels in scrape(app)}
        assert not any(route and ("ATL" in route or "ZZZ" in route) for route in routes)

    async def test_unmatched_paths_share_one_label(self, counted: AsyncClient) -> None:
        await counted.get("/no-such-thing/123")
        await counted.get("/no-such-thing/456")

        app = app_of(counted)
        assert (
            sample(app, "http_requests_total", method="GET", route="unmatched", status="404") == 2
        )

    async def test_a_made_up_method_is_other(self, counted: AsyncClient) -> None:
        await counted.request("BREW", "/health")

        app = app_of(counted)
        assert sample(
            app, "http_requests_total", method="other", route="/api/v1/health", status="405"
        )

    async def test_counters_move(self, counted: AsyncClient) -> None:
        app = app_of(counted)
        labels = {"method": "GET", "route": "/api/v1/health", "status": "200"}
        before = sample(app, "http_requests_total", **labels) or 0

        await counted.get("/health")
        await counted.get("/health")

        assert sample(app, "http_requests_total", **labels) == before + 2
        assert (
            sample(app, "http_request_duration_seconds_count", method="GET", route="/api/v1/health")
            == before + 2
        )
        assert sample(app, "http_requests_in_flight") == 0


class TestWhatIsReadAtScrapeTime:
    async def test_the_dashboard_counters(self, counted: AsyncClient) -> None:
        await counted.get("/projects/ATL/summary")
        counted.headers["Authorization"] = "Bearer not-a-token"
        await counted.get("/projects/ATL/summary")

        app = app_of(counted)
        assert sample(app, "cylist_auth_refusals_total", reason="no_credential_presented") == 1
        assert sample(app, "cylist_auth_refusals_total", reason="malformed_credential") == 1
        assert sample(app, "cylist_unhandled_500_total") == 0
        assert sample(app, "cylist_hook_deliveries_failed_total") == 0

        app.state.metrics.rate_limit_hits["address"] += 3
        assert sample(app, "cylist_rate_limit_hits_total", bucket="address") == 3

    async def test_the_realtime_counts(self, counted: AsyncClient) -> None:
        app = app_of(counted)
        assert sample(app, "cylist_ws_agent_connections") == 0
        assert sample(app, "cylist_ws_board_watchers") == 0

    async def test_the_pool(self, counted: AsyncClient, settings: Settings) -> None:
        await counted.get("/health")

        app = app_of(counted)
        assert sample(app, "cylist_db_pool_size") == settings.database_pool_size
        assert sample(app, "cylist_db_pool_max_overflow") == settings.database_max_overflow
        assert sample(app, "cylist_db_pool_in_use") == 0
        assert sample(app, "cylist_db_pool_waiting") == 0
        assert (sample(app, "cylist_db_pool_wait_seconds_count") or 0) >= 1

    async def test_the_listen_line_is_down_without_the_lifespan(self, counted: AsyncClient) -> None:
        assert sample(app_of(counted), "cylist_listen_connection_up") == 0

    async def test_the_blob_store(self, counted: AsyncClient, settings: Settings) -> None:
        settings.blob_dir.mkdir(parents=True, exist_ok=True)
        free = sample(app_of(counted), "cylist_blob_store_free_bytes")
        assert free is not None
        assert free > 0


async def test_the_loop_lag_sees_a_blocked_loop(settings: Settings) -> None:
    prometheus: Prometheus = create_app(metrics_on(settings)).state.prometheus
    watching = asyncio.create_task(prometheus.watch_the_loop(every=0.01))
    try:
        await asyncio.sleep(0.02)
        time.sleep(0.1)  # noqa: ASYNC251 - blocking the loop is the point
        # The gauge is the latest sample, and the next on-time wake replaces
        # it; so look between turns of the loop, not after a sleep of our own.
        lags: list[float] = []
        for _ in range(10):
            await asyncio.sleep(0)
            lags.append(prometheus.registry.get_sample_value("cylist_event_loop_lag_seconds") or 0)
        assert max(lags) >= 0.05
    finally:
        watching.cancel()
        with pytest.raises(asyncio.CancelledError):
            await watching


class TestThePortSplit:
    async def test_metrics_are_served_on_their_own_port_only(self, settings: Settings) -> None:
        port = free_port()
        configured = metrics_on(settings, metrics_port=port, agent_reap_every_seconds=0.5)
        app = create_app(configured)
        async with app.router.lifespan_context(app):
            await settled(app)
            public = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
            async with public, httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as private:
                assert (await public.get("/api/v1/health")).status_code == 200
                assert (await public.get("/metrics")).status_code == 404

                scraped = await private.get("/metrics")
                assert scraped.status_code == 200
                assert scraped.headers["content-type"].startswith("text/plain")
                assert 'route="/api/v1/health"' in scraped.text
                assert "process_resident_memory_bytes" in scraped.text

                # The listener serves the metrics and nothing else.
                assert (await private.get("/api/v1/health")).status_code == 404

                assert sample(app, "cylist_listen_connection_up") == 1
                assert (sample(app, "cylist_db_pool_wait_seconds_count") or 0) >= 1

                # The reaper reports each pass. Stopped as soon as one has,
                # so the next is half a second away rather than under way.
                for _ in range(200):
                    if sample(app, "cylist_reaper_last_run_timestamp_seconds"):
                        break
                    await asyncio.sleep(0.01)
                assert sample(app, "cylist_reaper_last_run_timestamp_seconds")

        with pytest.raises(OSError):
            socket.create_connection(("127.0.0.1", port), timeout=1).close()

    async def test_the_spa_does_not_answer_for_it(
        self, settings: Settings, database: Database, tmp_path: Path
    ) -> None:
        web = tmp_path / "web"
        web.mkdir()
        (web / "index.html").write_text("<!doctype html><title>Cylist</title>", "utf-8")
        configured = metrics_on(settings, web_dir=web)
        app = create_app(configured)
        app.state.database = database
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
            assert (await http.get("/p/ATL/board")).status_code == 200
            assert (await http.get("/metrics")).status_code == 404

        # And the SPA's deep links are one label, not one per path.
        assert sample(app, "http_requests_total", method="GET", route="/{path:path}", status="200")


async def test_the_pool_counts_who_is_waiting_and_for_how_long(settings: Settings) -> None:
    database = Database.configured(
        settings.model_copy(update={"database_pool_size": 1, "database_max_overflow": 0})
    )
    try:
        async with database.session() as holding:
            await holding.execute(select(1))
            assert database.pool_status().in_use == 1

            async def second() -> None:
                async with database.session() as waiting:
                    await waiting.execute(select(1))

            queued = asyncio.create_task(second())
            for _ in range(100):
                if database.pool_status().waiting == 1:
                    break
                await asyncio.sleep(0.01)
            assert database.pool_status().waiting == 1
            await asyncio.sleep(0.1)
        await queued

        status = database.pool_status()
        assert (status.waiting, status.in_use, status.waits) == (0, 0, 2)
        assert status.wait_seconds >= 0.1
    finally:
        await database.dispose()

    # Disposing swaps in a new pool, which carries on the same counts.
    assert database.pool_status().waits == 2
