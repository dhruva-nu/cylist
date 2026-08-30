"""Configuration, and the ``GET /me`` that decides what gets registered."""

from __future__ import annotations

import httpx
import pytest

from cylist_mcp import config
from cylist_mcp.__main__ import scopes_of
from cylist_mcp.client import ApiClient
from cylist_mcp.errors import CylistError
from cylist_mcp.server import build_server
from tests import fake_api


def test_a_missing_token_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fourteen tools that all return 401 would be worse than not starting."""
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    with pytest.raises(CylistError) as error:
        config.from_environment()
    assert "CYLIST_TOKEN is not set" in error.value.message
    assert "read,write" in error.value.message


def test_the_url_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_token")
    monkeypatch.delenv("CYLIST_URL", raising=False)
    assert config.from_environment().url == "http://localhost:8000"


def test_a_trailing_slash_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_token")
    monkeypatch.setenv("CYLIST_URL", "https://cylist.example.com/")
    assert config.from_environment().url == "https://cylist.example.com"


async def test_scopes_come_from_the_server_not_from_configuration() -> None:
    """The token's real authority decides the toolset, not a local setting."""
    recorder = fake_api.Recorder()
    transport = fake_api.build(recorder, scopes=["read", "write", "vault:reveal"])
    async with ApiClient("http://cylist.test", "cyl_token", transport=transport) as client:
        scopes = await scopes_of(client)

    assert scopes == frozenset({"read", "write", "vault:reveal"})
    assert "GET /api/v1/me" in recorder.paths()

    names = {tool.name for tool in await build_server(client, scopes).list_tools()}
    assert "reveal_secret" in names


async def test_a_rejected_token_fails_loudly() -> None:
    recorder = fake_api.Recorder()
    transport = fake_api.build(
        recorder,
        overrides={
            ("GET", "/me"): httpx.Response(
                401,
                json={
                    "error": {
                        "code": "unauthorized",
                        "message": "That token is not valid.",
                        "details": {},
                    }
                },
            )
        },
    )
    async with ApiClient("http://cylist.test", "cyl_bad", transport=transport) as client:
        with pytest.raises(CylistError) as error:
            await scopes_of(client)

    assert "CYLIST_TOKEN" in error.value.message
