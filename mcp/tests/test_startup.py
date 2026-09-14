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


def _write_config(body: str) -> None:
    """Write the file ``cylist setup`` would have written, in the tmp HOME."""
    path = config.config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, "utf-8")


def test_a_missing_token_is_refused_at_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    """Thirty tools that all return 401 would be worse than not starting."""
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    with pytest.raises(CylistError) as error:
        config.resolve()
    assert "cylist setup" in error.value.message
    assert "CYLIST_TOKEN" in error.value.message
    assert "read,write" in error.value.message


def test_the_url_defaults_to_localhost(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_token")
    monkeypatch.delenv("CYLIST_URL", raising=False)
    assert config.resolve().urls == ("http://localhost:8000",)


def test_a_trailing_slash_is_trimmed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_token")
    monkeypatch.setenv("CYLIST_URL", "https://cylist.example.com/")
    assert config.resolve().url == "https://cylist.example.com"


def test_the_cli_config_file_supplies_the_token_and_every_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The point of it: an MCP registration that holds no credential at all."""
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    monkeypatch.delenv("CYLIST_URL", raising=False)
    _write_config(
        'url = "http://localhost:8000"\n'
        'urls = ["http://localhost:8000", "https://box.tailnet.ts.net"]\n'
        'token = "cyl_from_the_file"\n'
    )

    settings = config.resolve()

    assert settings.token == "cyl_from_the_file"
    assert settings.urls == ("http://localhost:8000", "https://box.tailnet.ts.net")


def test_the_environment_beats_the_file(monkeypatch: pytest.MonkeyPatch) -> None:
    """An MCP client that names a server means that server, and that token."""
    _write_config(
        'url = "http://localhost:8000"\n'
        'urls = ["http://localhost:8000", "https://box.tailnet.ts.net"]\n'
        'token = "cyl_from_the_file"\n'
    )
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_from_the_env")
    monkeypatch.setenv("CYLIST_URL", "https://elsewhere.example.com")

    settings = config.resolve()

    assert settings.token == "cyl_from_the_env"
    assert settings.urls == ("https://elsewhere.example.com",)


def test_an_unreadable_config_file_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    """The environment may hold everything; a stdio server cannot complain."""
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_token")
    monkeypatch.delenv("CYLIST_URL", raising=False)
    _write_config("this is not toml {{{")

    assert config.resolve().urls == ("http://localhost:8000",)


async def test_a_second_address_is_tried_when_the_first_will_not_connect() -> None:
    """What makes a laptop that has left the tailnet keep working."""
    recorder = fake_api.Recorder()
    answering = fake_api.build(recorder)

    def transport(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gone.test":
            raise httpx.ConnectError("connection refused", request=request)
        answered = answering.handler(request)
        assert isinstance(answered, httpx.Response)  # the fake is synchronous
        return answered

    async with ApiClient(
        ["http://gone.test", "http://cylist.test"],
        "cyl_token",
        transport=httpx.MockTransport(transport),
    ) as client:
        assert await scopes_of(client) == frozenset({"read", "write"})
        assert client.url == "http://cylist.test"


async def test_a_failure_after_connecting_is_not_retried_elsewhere() -> None:
    """It might be a write that was applied and whose response was lost."""
    calls: list[str] = []

    def transport(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url.host))
        raise httpx.ReadTimeout("too slow", request=request)

    async with ApiClient(
        ["http://first.test", "http://second.test"],
        "cyl_token",
        transport=httpx.MockTransport(transport),
    ) as client:
        with pytest.raises(CylistError) as error:
            await client.get("/me")

    assert calls == ["first.test"]
    assert "first.test" in error.value.message


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
