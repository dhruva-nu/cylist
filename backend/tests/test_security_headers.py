"""The headers that tell a browser what it may do with a response.

CYLIST-136. Every response carries them — the API's JSON, its refusals, the
SPA and its assets — and a download carries stricter ones of its own: it is
somebody's bytes, it must not be sniffed into something runnable, and a
restricted file must not be kept by a cache.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient, Response

from app.config import Settings
from app.core.security_headers import DOCS_POLICY, HSTS, spa_policy
from app.db import Database
from app.main import create_app
from tests.conftest import client_for

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
INDEX = "<!doctype html><title>Cylist</title><div id=root></div>"


def assert_guarded(response: Response) -> None:
    """The headers every response has, whatever it is."""
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "strict-origin-when-cross-origin"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


async def root_id(client: AsyncClient) -> str:
    folder_id: str = (await client.get("/projects/ATL/tree")).json()["id"]
    return folder_id


@pytest.fixture
async def project(signed_in: AsyncClient) -> AsyncClient:
    await signed_in.post("/projects", json=ATLAS)
    return signed_in


@pytest.fixture
async def site(
    settings: Settings, database: Database, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """A client at the site root, for an app serving a built SPA."""
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text(INDEX, encoding="utf-8")
    (web / "assets" / "app.js").write_text("console.log('cylist')\n", encoding="utf-8")
    configured = settings.model_copy(update={"web_dir": web})
    app = create_app(configured)
    app.state.database = database
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        yield http


class TestEveryResponse:
    async def test_an_api_answer(self, client: AsyncClient) -> None:
        assert_guarded(await client.get("/health"))

    async def test_a_refusal(self, client: AsyncClient) -> None:
        response = await client.get("/projects")

        assert response.status_code == 401
        assert_guarded(response)

    async def test_a_body_refused_for_its_size(self, client: AsyncClient) -> None:
        response = await client.post(
            "/projects", content=b"{}", headers={"content-length": str(64 << 20)}
        )

        assert response.status_code == 413
        assert_guarded(response)

    async def test_the_app_and_its_assets(self, site: AsyncClient) -> None:
        for path in ("/", "/p/ATL/board", "/assets/app.js"):
            response = await site.get(path)
            assert response.status_code == 200
            assert_guarded(response)

    async def test_the_app_gets_the_spas_policy(self, site: AsyncClient) -> None:
        policy = (await site.get("/")).headers["content-security-policy"]

        assert policy == spa_policy(allow_plain_sockets=True)
        assert "script-src 'self'" in policy
        assert "unsafe-inline" not in policy
        assert "https://fonts.googleapis.com" in policy
        assert "https://fonts.gstatic.com" in policy

    async def test_the_api_docs_get_a_policy_that_lets_them_load(self, site: AsyncClient) -> None:
        response = await site.get("/api/v1/docs")

        assert response.status_code == 200
        assert response.headers["content-security-policy"] == DOCS_POLICY
        assert "https://cdn.jsdelivr.net" in DOCS_POLICY


class TestStrictTransportSecurity:
    async def test_is_not_sent_off_a_deployment(self, client: AsyncClient) -> None:
        response = await client.get("/health")

        assert "strict-transport-security" not in response.headers
        assert "ws:" in response.headers["content-security-policy"]

    @pytest.mark.parametrize("environment", ["preview", "staging", "prod"])
    async def test_is_sent_by_a_deployment(
        self, settings: Settings, database: Database, environment: str
    ) -> None:
        deployed = settings.model_copy(update={"environment": environment})
        async with client_for(deployed, database) as http:
            response = await http.get("/health")

        assert response.headers["strict-transport-security"] == HSTS
        policy = response.headers["content-security-policy"]
        assert "connect-src 'self' wss:" in policy
        assert " ws:" not in policy


class TestADownload:
    async def test_is_not_sniffed_or_cached(self, project: AsyncClient) -> None:
        folder = await root_id(project)
        uploaded = await project.post(
            f"/folders/{folder}/upload",
            files={"file": ("brief.pdf", b"%PDF a restricted brief", "application/pdf")},
            data={"sensitivity": "restricted"},
        )
        assert uploaded.status_code == 201, uploaded.text

        response = await project.get(f"/items/{uploaded.json()['id']}/download")

        assert response.status_code == 200
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-security-policy"] == "default-src 'none'; sandbox"
        assert response.headers["content-disposition"].startswith("attachment")

    async def test_a_skill_is_not_sniffed_or_cached(self, project: AsyncClient) -> None:
        uploaded = await project.post(
            "/projects/ATL/skills", files={"file": ("tidy.md", b"Tidy.\n", "text/markdown")}
        )

        response = await project.get(f"/skills/{uploaded.json()['id']}/download")

        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"] == "private, no-store"
        assert response.headers["content-type"].startswith("text/markdown")


class TestTheStoredType:
    @pytest.mark.parametrize(
        ("declared", "stored"),
        [
            ("application/pdf", "application/pdf"),
            ("Text/HTML; charset=utf-8", "text/html"),
            ("text/plain;\r\nX-Injected: yes", "text/plain"),
            ("not a type", "application/octet-stream"),
            ("image/png<script>", "application/octet-stream"),
            ("a/" + "b" * 300, "application/octet-stream"),
        ],
    )
    async def test_is_normalised_at_upload(
        self, project: AsyncClient, declared: str, stored: str
    ) -> None:
        folder = await root_id(project)
        boundary = "b0undary"
        body = (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="f.bin"\r\n'
            f"Content-Type: {declared}\r\n\r\n"
            f"content for {declared!r}\r\n"
            f"--{boundary}--\r\n"
        ).encode()

        response = await project.post(
            f"/folders/{folder}/upload",
            content=body,
            headers={"content-type": f"multipart/form-data; boundary={boundary}"},
        )

        assert response.status_code == 201, response.text
        assert response.json()["mime"] == stored
