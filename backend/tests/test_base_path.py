"""Serving the whole app under a base path, as a dev slot does (``/dev_1``).

A request can arrive two ways: with the prefix on (run locally, no proxy) or
with it taken off (``tailscale serve --set-path`` strips it). Both have to be
the same request, and everything handed back to the browser — asset URLs, the
cookie, links — has to carry the prefix, so nothing ever asks the root.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from httpx import ASGITransport, AsyncClient
from mcp.client import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.types import CallToolResult, TextContent
from pydantic import ValidationError

from app.config import SESSION_COOKIE, Settings
from app.core.base_path import route_path
from app.db import Database
from app.main import create_app
from app.spa import render_index
from tests import ws
from tests.conftest import OWNER_EMAIL, OWNER_PASSWORD, ensure_owner

BASE = "/dev_1"

INDEX = """<!doctype html>
<html lang="en">
  <head>
    <meta name="cylist-base-path" content="" />
    <title>Cylist</title>
    <script type="module" crossorigin src="./assets/index-abc.js"></script>
    <link rel="stylesheet" crossorigin href="./assets/index-abc.css">
    <link rel="preconnect" href="https://fonts.googleapis.com" />
  </head>
  <body><div id="root"></div></body>
</html>
"""
"""Shaped like ``npm run build`` output now that Vite builds with ``base: './'``."""


@pytest.fixture
def built(tmp_path: Path) -> Path:
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(INDEX, encoding="utf-8")
    (web / "assets" / "index-abc.js").write_text("console.log('cylist')\n", encoding="utf-8")
    return web


@asynccontextmanager
async def slot(
    settings: Settings, database: Database, web: Path, *, base_url: str
) -> AsyncIterator[AsyncClient]:
    """A client for an app configured with ``CYLIST_BASE_PATH=/dev_1``."""
    configured = settings.model_copy(update={"web_dir": web, "base_path": BASE})
    app = create_app(configured)
    app.state.settings = configured
    app.state.database = database
    database.publish_to(app.state.hub.publish)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url=base_url) as http:
            http.app = app  # type: ignore[attr-defined]
            yield http
    finally:
        database.publish_to(None)


@pytest.fixture
async def prefixed(
    settings: Settings, database: Database, built: Path
) -> AsyncIterator[AsyncClient]:
    """The browser's view with no proxy in front: every path says /dev_1."""
    async with slot(settings, database, built, base_url=f"http://test{BASE}") as http:
        yield http


@pytest.fixture
async def stripped(
    settings: Settings, database: Database, built: Path
) -> AsyncIterator[AsyncClient]:
    """The app's view behind tailscale, which took /dev_1 off on the way in."""
    async with slot(settings, database, built, base_url="http://test") as http:
        yield http


async def sign_in_at(client: AsyncClient) -> AsyncClient:
    await ensure_owner(client)
    response = await client.post(
        "/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
    )
    assert response.status_code == 200, response.text
    if BASE not in str(client.base_url):
        # Behind the proxy the browser's address still says /dev_1, so it
        # sends the cookie; this client addresses the app as the proxy does,
        # without the prefix, and has to be handed it.
        value = client.cookies["cylist_session_dev_1"]
        client.cookies.clear()
        client.cookies.set("cylist_session_dev_1", value)
    return client


class TestTheSetting:
    def test_is_normalised_to_one_leading_slash(self) -> None:
        for raw in ("/dev_1", "dev_1", "/dev_1/", " /dev_1 "):
            assert Settings(base_path=raw).base_path == "/dev_1"

    def test_the_root_is_empty(self) -> None:
        assert Settings().base_path == ""
        assert Settings(base_path="/").base_path == ""

    def test_anything_but_a_plain_path_is_refused(self) -> None:
        for raw in ('/dev"1', "/dev 1", "/dev_1?x", "/a//b"):
            with pytest.raises(ValidationError):
                Settings(base_path=raw)

    def test_the_cookie_is_unchanged_at_the_root(self) -> None:
        root = Settings()
        assert root.session_cookie_name == SESSION_COOKIE == "cylist_session"
        assert root.session_cookie_path == "/"

    def test_each_slot_has_its_own_cookie(self) -> None:
        one, two = Settings(base_path="/dev_1"), Settings(base_path="/dev_2")
        assert one.session_cookie_name == "cylist_session_dev_1"
        assert two.session_cookie_name == "cylist_session_dev_2"
        assert one.session_cookie_path == "/dev_1"


class TestRoutePath:
    def test_takes_the_base_path_off(self) -> None:
        scope = {"path": "/dev_1/api/v1/health", "root_path": "/dev_1"}
        assert route_path(scope) == "/api/v1/health"

    def test_is_the_path_at_the_root(self) -> None:
        assert route_path({"path": "/api/v1/health", "root_path": ""}) == "/api/v1/health"

    def test_leaves_a_longer_name_alone(self) -> None:
        scope = {"path": "/dev_10/x", "root_path": "/dev_1"}
        assert route_path(scope) == "/dev_10/x"


class TestIndex:
    def test_assets_and_meta_follow_the_base_path(self) -> None:
        rendered = render_index(INDEX, BASE)

        assert 'src="/dev_1/assets/index-abc.js"' in rendered
        assert 'href="/dev_1/assets/index-abc.css"' in rendered
        assert '<meta name="cylist-base-path" content="/dev_1" />' in rendered
        assert "./assets" not in rendered
        # An absolute URL elsewhere is somebody else's and stays as it was.
        assert 'href="https://fonts.googleapis.com"' in rendered

    def test_the_root_renders_what_a_root_build_would(self) -> None:
        rendered = render_index(INDEX, "")

        assert 'src="/assets/index-abc.js"' in rendered
        assert '<meta name="cylist-base-path" content="" />' in rendered

    def test_a_build_without_the_tag_is_given_one(self) -> None:
        rendered = render_index("<html><head></head><body></body></html>", BASE)
        assert 'content="/dev_1"' in rendered


@pytest.mark.parametrize("which", ["prefixed", "stripped"])
class TestEitherWayIn:
    """What the browser sees is the same whether or not the proxy stripped /dev_1."""

    @pytest.fixture
    def http(self, request: pytest.FixtureRequest, which: str) -> AsyncClient:
        client: AsyncClient = request.getfixturevalue(which)
        return client

    async def test_the_app_names_its_assets_under_the_base_path(self, http: AsyncClient) -> None:
        for path in ("/", "/p/ATL/board"):
            response = await http.get(path)

            assert response.status_code == 200
            assert 'src="/dev_1/assets/index-abc.js"' in response.text
            assert 'content="/dev_1"' in response.text

    async def test_the_assets_are_served(self, http: AsyncClient) -> None:
        response = await http.get("/assets/index-abc.js")

        assert response.status_code == 200
        assert "cylist" in response.text

    async def test_the_api_answers_and_a_typo_is_still_a_404(self, http: AsyncClient) -> None:
        assert (await http.get("/api/v1/health")).json()["status"] == "ok"

        typo = await http.get("/api/v1/porjects")
        assert typo.status_code == 404
        assert "text/html" not in typo.headers["content-type"]

    async def test_the_docs_point_at_the_prefixed_contract(self, http: AsyncClient) -> None:
        response = await http.get("/api/v1/docs")

        assert "/dev_1/api/v1/openapi.json" in response.text

    async def test_an_invitation_links_under_the_base_path(self, http: AsyncClient) -> None:
        await sign_in_at(http)
        person = (
            await http.post(
                "/api/v1/people",
                json={
                    "name": "Aditi K",
                    "kind": "team",
                    "title": "Designer",
                    "responsibilities": "The board.",
                    "email": "aditi@example.com",
                },
            )
        ).json()

        issued = (await http.post(f"/api/v1/people/{person['id']}/invite")).json()

        assert issued["url"] == f"http://test/dev_1/invite/{issued['token']}"


class TestTheSlotsFrontDoor:
    async def test_the_bare_prefix_is_the_app(
        self, settings: Settings, database: Database, built: Path
    ) -> None:
        async with slot(settings, database, built, base_url="http://test") as http:
            response = await http.get(BASE)

        assert response.status_code == 200
        assert 'src="/dev_1/assets/index-abc.js"' in response.text


class TestTheSession:
    async def test_the_cookie_is_named_and_scoped_for_the_slot(self, prefixed: AsyncClient) -> None:
        await ensure_owner(prefixed)
        response = await prefixed.post(
            "/api/v1/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD}
        )

        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith("cylist_session_dev_1=")
        assert "Path=/dev_1" in set_cookie
        # And the browser, holding it, is signed in under the prefix.
        assert (await prefixed.get("/api/v1/me")).status_code == 200

    async def test_another_deployments_cookie_is_not_read(self, prefixed: AsyncClient) -> None:
        """Staging's cookie reaches a dev slot — same host — and means nothing there."""
        await sign_in_at(prefixed)
        token = prefixed.cookies["cylist_session_dev_1"]
        prefixed.cookies.clear()
        prefixed.cookies.set(SESSION_COOKIE, token)

        assert (await prefixed.get("/api/v1/me")).status_code == 401

    async def test_signing_out_clears_the_slots_cookie(self, prefixed: AsyncClient) -> None:
        await sign_in_at(prefixed)

        response = await prefixed.post("/api/v1/auth/logout")

        set_cookie = response.headers["set-cookie"]
        assert set_cookie.startswith("cylist_session_dev_1=")
        assert "Path=/dev_1" in set_cookie
        assert (await prefixed.get("/api/v1/me")).status_code == 401


class TestUnderThePrefix:
    async def test_a_file_downloads(self, prefixed: AsyncClient) -> None:
        await sign_in_at(prefixed)
        await prefixed.post("/api/v1/projects", json={"key": "ATL", "name": "Atlas"})
        root = (await prefixed.get("/api/v1/projects/ATL/tree")).json()["id"]
        content = b"cylist under a prefix\n" * 8
        uploaded = await prefixed.post(
            f"/api/v1/folders/{root}/upload",
            files={"file": ("notes.txt", content, "text/plain")},
        )
        assert uploaded.status_code == 201, uploaded.text

        download = await prefixed.get(f"/api/v1/items/{uploaded.json()['id']}/download")

        assert download.status_code == 200
        assert download.content == content

    @pytest.mark.parametrize(
        "path", ["/dev_1/api/v1/projects/ATL/board/ws", "/api/v1/projects/ATL/board/ws"]
    )
    async def test_the_board_socket_opens(self, prefixed: AsyncClient, path: str) -> None:
        await sign_in_at(prefixed)
        await prefixed.post("/api/v1/projects", json={"key": "ATL", "name": "Atlas"})
        cookie = f"cylist_session_dev_1={prefixed.cookies['cylist_session_dev_1']}"

        async with ws.connect(prefixed.app, path, cookies=cookie) as socket:  # type: ignore[attr-defined]
            assert await socket.receive_json() == {"type": "ready"}

    async def test_the_mcp_tools_answer(self, prefixed: AsyncClient) -> None:
        """At /dev_1/mcp, and their in-process calls to the API — which go
        unprefixed — land on the same app."""
        await sign_in_at(prefixed)
        await prefixed.post("/api/v1/projects", json={"key": "ATL", "name": "Atlas"})
        issued = await prefixed.post(
            "/api/v1/tokens", json={"name": "Claude Code", "scopes": ["read", "write"]}
        )
        token = issued.json()["token"]
        app = prefixed.app  # type: ignore[attr-defined]

        async with (
            app.state.mcp.run(),
            httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app),
                headers={"Authorization": f"Bearer {token}"},
            ) as http,
            Client(
                streamable_http_client(f"http://test{BASE}/mcp", http_client=http), mode="legacy"
            ) as mcp,
        ):
            listed = await mcp.call_tool("list_projects", {})

        assert isinstance(listed, CallToolResult)
        assert not listed.is_error, listed.content
        text = "".join(block.text for block in listed.content if isinstance(block, TextContent))
        assert "ATL" in text
