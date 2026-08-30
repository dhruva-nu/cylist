"""Fixtures: an isolated config directory and a way to run a command."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

from cylist_cli import output
from cylist_cli.main import main
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
