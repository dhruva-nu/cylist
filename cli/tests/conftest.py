"""Fixtures: an isolated config directory, a way to run a command, and a transport."""

from __future__ import annotations

import socket
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from cylist_cli import output
from cylist_cli.main import main
from cylist_cli.presence import ipc
from tests import fake_api


@dataclass
class Result:
    """What a command left behind."""

    code: int
    out: str
    err: str

    @property
    def lines(self) -> list[str]:
        return [line for line in self.out.splitlines() if line.strip()]


Runner = Callable[..., Result]


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the tests away from the developer's own token and server.

    Without this, a run on a machine that has ``cylist login``-ed would read a
    real token out of ``~/.config`` and, worse, a real ``CYLIST_URL`` — and a
    test that is supposed to hit the fake transport would be reaching for a
    live server.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("CYLIST_TOKEN", "cyl_test_token")
    monkeypatch.setenv("CYLIST_URL", "http://cylist.test")
    # A fixed width keeps the board and table assertions independent of the
    # terminal the suite happens to be run in.
    monkeypatch.setattr(output, "terminal_width", lambda: 120)


@pytest.fixture
def recorder() -> fake_api.Recorder:
    return fake_api.Recorder()


@pytest.fixture
def run(recorder: fake_api.Recorder, capsys: pytest.CaptureFixture[str]) -> Iterator[Runner]:
    """Run ``cylist …`` against the fake API and capture what it printed."""

    def invoke(
        *argv: str,
        overrides: dict[tuple[str, str], httpx.Response] | None = None,
    ) -> Result:
        transport = fake_api.build(recorder, overrides=overrides)
        code = main(list(argv), transport=transport)
        captured = capsys.readouterr()
        return Result(code=code, out=captured.out, err=captured.err)

    yield invoke


@pytest.fixture(params=["unix", "tcp"])
def ipc_transport(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> str:
    """Run the test once per hook-to-daemon carrier.

    Not autouse: only the presence modules want it. What it buys is that the
    loopback transport — the one Windows uses, because CPython has no
    ``AF_UNIX`` there — is exercised on every Linux run as well. A path that
    only ever runs on the platform nobody develops on is a path that is
    never really tested.
    """
    if request.param == "unix" and not hasattr(socket, "AF_UNIX"):
        pytest.skip("this platform has no AF_UNIX")
    monkeypatch.setenv(ipc.TRANSPORT_ENV, request.param)
    return str(request.param)
