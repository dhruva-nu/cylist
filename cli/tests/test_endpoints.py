"""Several addresses for one server: the order, the failover, the memory.

This is what makes a machine that set itself up on a tailnet keep working
after it leaves, and stop paying for the public hostname when it comes back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from cylist_cli import config as configuration
from cylist_cli import endpoints
from cylist_cli.client import Client
from cylist_cli.errors import CylistError

HOME = "http://localhost:8000"
TAILNET = "https://box.tailnet.test"
PUBLIC = "https://box.example.com"


@pytest.fixture(autouse=True)
def state_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


def _refuse(*hosts: str) -> httpx.MockTransport:
    """A transport that will not connect to ``hosts`` and answers otherwise."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host in hosts:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={"reached": str(request.url)})

    return httpx.MockTransport(handler)


# --- The order they are tried in -------------------------------------------


def test_the_configured_order_is_used_when_nothing_is_remembered() -> None:
    assert endpoints.order([HOME, TAILNET]) == (HOME, TAILNET)


def test_the_address_that_last_answered_is_tried_first() -> None:
    endpoints.remember(TAILNET)

    assert endpoints.order([HOME, TAILNET]) == (TAILNET, HOME)


def test_a_remembered_address_that_is_no_longer_configured_is_ignored() -> None:
    """A hint about order, never a source of addresses."""
    endpoints.remember("https://somewhere-else.test")

    assert endpoints.order([HOME, TAILNET]) == (HOME, TAILNET)


def test_the_memory_expires_so_a_laptop_that_comes_home_goes_local_again() -> None:
    stale = datetime.now(UTC) - endpoints.MEMORY_TTL - timedelta(minutes=1)
    endpoints.memory_path().parent.mkdir(parents=True, exist_ok=True)
    endpoints.memory_path().write_text(json.dumps({"url": PUBLIC, "at": stale.isoformat()}))

    assert endpoints.remembered() is None
    assert endpoints.order([HOME, PUBLIC]) == (HOME, PUBLIC)


def test_a_corrupt_memory_is_no_memory(tmp_path: Path) -> None:
    endpoints.memory_path().parent.mkdir(parents=True, exist_ok=True)
    endpoints.memory_path().write_text("{not json")

    assert endpoints.remembered() is None


def test_remembering_never_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A state directory that cannot be written is not a reason for a command
    to fail — it costs the ordering and nothing else.

    The unwritable place is a directory whose parent is a *file*, which every
    platform refuses with an OSError. A hard-coded ``/proc/…`` said Linux, and
    on Windows it was an ordinary relative path that the test cheerfully
    created — so this passed there by not testing anything.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("")
    monkeypatch.setenv("XDG_STATE_HOME", str(blocker / "state"))

    endpoints.remember(TAILNET)  # no exception

    assert endpoints.remembered() is None


# --- Falling over to the next one ------------------------------------------


def test_a_refused_address_is_stepped_over() -> None:
    with Client([HOME, TAILNET], "cyl_token", transport=_refuse("localhost")) as client:
        client.get("/projects")

        assert client.url == TAILNET


def test_the_address_that_answered_is_kept_for_the_next_request() -> None:
    """A session of commands pays the search once, not once per call."""
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url.host))
        if request.url.host == "localhost":
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200, json={})

    with Client([HOME, TAILNET], "cyl_token", transport=httpx.MockTransport(handler)) as client:
        client.get("/projects")
        client.get("/people")

    assert tried == ["localhost", "box.tailnet.test", "box.tailnet.test"]


def test_the_address_that_answered_is_remembered_for_the_next_command() -> None:
    with Client([HOME, TAILNET], "cyl_token", transport=_refuse("localhost")) as client:
        client.get("/projects")

    assert endpoints.remembered() == TAILNET


def test_a_failure_after_connecting_is_not_retried_elsewhere() -> None:
    """It may be a write that was applied and whose response was lost, and
    sending it again could create a second card."""
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url.host))
        raise httpx.ReadTimeout("too slow", request=request)

    with (
        Client([HOME, TAILNET], "cyl_token", transport=httpx.MockTransport(handler)) as client,
        pytest.raises(CylistError) as error,
    ):
        client.post("/projects", {"key": "ATL"})

    assert tried == ["localhost"]
    assert "localhost:8000" in error.value.message


def test_a_server_that_is_nowhere_names_everything_it_tried() -> None:
    with (
        Client(
            [HOME, TAILNET, PUBLIC],
            "cyl_token",
            transport=_refuse("localhost", "box.tailnet.test", "box.example.com"),
        ) as client,
        pytest.raises(CylistError) as error,
    ):
        client.get("/projects")

    for address in (HOME, TAILNET, PUBLIC):
        assert address in error.value.message


def test_one_address_still_reports_the_old_single_line_message() -> None:
    """Nothing about a list should turn 'cannot reach X' into a list of one."""
    with (
        Client(HOME, "cyl_token", transport=_refuse("localhost")) as client,
        pytest.raises(CylistError) as error,
    ):
        client.get("/projects")

    assert error.value.message == f"Cannot reach the Cylist API at {HOME}."


def test_a_budget_stops_the_walk_so_a_hook_cannot_pay_three_timeouts() -> None:
    """The Claude Code hook is on the path of every prompt: N addresses may
    not cost N timeouts, however many are configured."""
    tried: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        tried.append(str(request.url.host))
        raise httpx.ConnectTimeout("timed out", request=request)

    with (
        Client(
            [HOME, TAILNET, PUBLIC],
            "cyl_token",
            transport=httpx.MockTransport(handler),
            budget=0.0,
        ) as client,
        pytest.raises(CylistError),
    ):
        client.get("/projects")

    # The first is always tried; the budget is spent by the time it fails.
    assert tried == ["localhost"]


def test_a_download_falls_over_too() -> None:
    with Client([HOME, TAILNET], "cyl_token", transport=_refuse("localhost")) as client:
        body = b"".join(client.stream("/items/x/download"))

    assert b"box.tailnet.test" in body


# --- What is stored, and what overrides it ---------------------------------


def test_the_list_survives_a_round_trip_through_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CYLIST_URL", raising=False)
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    path = tmp_path / "config.toml"

    configuration.save(HOME, "cyl_token", urls=[HOME, TAILNET], path=path)
    loaded = configuration.load(path=path)

    assert loaded.url == HOME
    assert loaded.urls == (HOME, TAILNET)
    assert loaded.token == "cyl_token"


def test_a_named_server_is_the_only_candidate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Naming a server means that server; reaching a different one because it
    was down would be the opposite of what was asked."""
    path = tmp_path / "config.toml"
    configuration.save(HOME, "cyl_token", urls=[HOME, TAILNET], path=path)

    monkeypatch.setenv("CYLIST_URL", PUBLIC)
    assert configuration.load(path=path).urls == (PUBLIC,)

    monkeypatch.delenv("CYLIST_URL")
    assert configuration.load(PUBLIC, path=path).urls == (PUBLIC,)


def test_a_hand_edited_list_with_rubbish_in_it_keeps_the_good_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CYLIST_URL", raising=False)
    path = tmp_path / "config.toml"
    path.write_text(
        f'url = "{HOME}"\nurls = ["{HOME}", 42, "", "{TAILNET}"]\ntoken = "cyl_token"\n'
    )

    assert configuration.load(path=path).urls == (HOME, TAILNET)


def test_a_config_file_from_before_the_list_existed_still_loads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Written by an older 'cylist login': one url, no list."""
    monkeypatch.delenv("CYLIST_URL", raising=False)
    path = tmp_path / "config.toml"
    path.write_text(f'url = "{TAILNET}"\ntoken = "cyl_token"\n')

    loaded = configuration.load(path=path)

    assert loaded.url == TAILNET
    assert loaded.urls == (TAILNET,)


# --- What the commands say about it ----------------------------------------


def test_whoami_names_the_address_that_answered(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Not the configured first one: "which server am I on" is a question of
    fact, and with several addresses the two answers differ."""
    from cylist_cli.main import main
    from tests import fake_api

    recorder = fake_api.Recorder()
    answering = fake_api.build(recorder)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "gone.test":
            raise httpx.ConnectError("connection refused", request=request)
        answered = answering.handler(request)
        assert isinstance(answered, httpx.Response)
        return answered

    path = tmp_path / "config.toml"
    configuration.save(
        "http://gone.test", "cyl_token", urls=["http://gone.test", "http://cylist.test"], path=path
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setattr(configuration, "config_path", lambda: path)
    monkeypatch.delenv("CYLIST_URL", raising=False)
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)

    code = main(["whoami"], transport=httpx.MockTransport(handler))
    printed = capsys.readouterr().out

    assert code == 0
    assert "Server  http://cylist.test" in printed
    assert "Also at  http://gone.test" in printed


def test_login_keeps_the_addresses_the_server_offers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A login is not a quieter way of throwing the list away."""
    import tomllib

    from cylist_cli.main import main
    from tests import fake_api

    path = tmp_path / "config.toml"
    monkeypatch.setattr(configuration, "config_path", lambda: path)
    monkeypatch.setattr("sys.stdin", _Line("cyl_pasted_token\n"))

    code = main(
        ["--url", "http://cylist.test", "login", "--token-stdin"],
        transport=fake_api.build(fake_api.Recorder()),
    )
    capsys.readouterr()

    assert code == 0
    stored = tomllib.loads(path.read_text("utf-8"))
    assert stored["urls"] == ["http://cylist.test", fake_api.TAILNET_URL]


class _Line:
    """Just enough of ``sys.stdin`` for ``--token-stdin``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def readline(self) -> str:
        return self._text
