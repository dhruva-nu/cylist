"""Serving the built SPA from the API process.

These tests build their own app rather than use the ``client`` fixture: that one
is based at ``/api/v1``, and everything here is about what happens outside it.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.db import Database
from app.main import create_app

INDEX = "<!doctype html><title>Cylist</title><div id=root></div>"


@pytest.fixture
def built(tmp_path: Path) -> Path:
    """A directory shaped like the output of ``npm run build``."""
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(INDEX, encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('cylist')\n", encoding="utf-8")
    return web


async def client_at(settings: Settings, database: Database, web: Path) -> AsyncClient:
    """A client based at the site root, for an app serving ``web``."""
    configured = settings.model_copy(update={"web_dir": web})
    app = create_app(configured)
    app.state.settings = configured
    app.state.database = database
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
async def web_client(
    settings: Settings, database: Database, built: Path
) -> AsyncIterator[AsyncClient]:
    async with await client_at(settings, database, built) as http:
        yield http


async def test_serves_the_app_at_the_root(web_client: AsyncClient) -> None:
    response = await web_client.get("/")

    assert response.status_code == 200
    assert response.text == INDEX


async def test_serves_the_built_assets(web_client: AsyncClient) -> None:
    response = await web_client.get("/assets/app.js")

    assert response.status_code == 200
    assert "cylist" in response.text


async def test_a_client_side_route_gets_the_app(web_client: AsyncClient) -> None:
    """Deep links and reloads land on paths only the browser router knows."""
    response = await web_client.get("/p/ATL/board")

    assert response.status_code == 200
    assert response.text == INDEX


async def test_the_api_is_still_served(web_client: AsyncClient) -> None:
    response = await web_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_an_unknown_api_path_is_a_404_not_the_app(web_client: AsyncClient) -> None:
    """An agent's typo must not be answered with a page of HTML."""
    response = await web_client.get("/api/v1/porjects")

    assert response.status_code == 404
    assert "text/html" not in response.headers["content-type"]


async def test_nothing_is_mounted_when_the_app_is_not_built(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    """Development: Vite serves the app, and this process only answers the API."""
    async with await client_at(settings, database, tmp_path / "never-built") as http:
        assert (await http.get("/")).status_code == 404
        assert (await http.get("/api/v1/health")).status_code == 200
