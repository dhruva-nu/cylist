"""``cylist work``: check the card, then hand the terminal to Claude Code."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import pytest

from cylist_cli.commands import work
from tests import fake_api
from tests.conftest import Runner


class Exec:
    """Stands in for ``execvpe``: records the hand-over instead of making it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[str], dict[str, str]]] = []

    def __call__(self, file: str, argv: Sequence[str], env: Mapping[str, str]) -> None:
        self.calls.append((file, list(argv), dict(env)))


@pytest.fixture
def execs(monkeypatch: pytest.MonkeyPatch) -> Exec:
    fake = Exec()
    monkeypatch.setattr(work, "_exec", fake)
    return fake


def test_work_checks_the_task_then_launches_claude_bound_to_it(
    run: Runner, recorder: fake_api.Recorder, execs: Exec
) -> None:
    result = run("work", "ATL-1")

    assert result.code == 0
    assert recorder.paths() == ["GET /api/v1/tasks/ATL-1"]
    [(file, argv, env)] = execs.calls
    assert file == "claude"
    assert argv == ["claude", "-n", "ATL-1"]
    assert env["CYLIST_TASK"] == "ATL-1"
    assert "Reconcile the ledger export" in result.err


def test_work_passes_extra_arguments_to_claude(run: Runner, execs: Exec) -> None:
    run("work", "ATL-1", "--", "--model", "opus", "-p", "hello")

    [(_, argv, _)] = execs.calls
    assert argv == ["claude", "-n", "ATL-1", "--model", "opus", "-p", "hello"]


def test_work_upper_cases_the_reference(run: Runner, execs: Exec) -> None:
    run("work", "atl-1")
    assert execs.calls[0][2]["CYLIST_TASK"] == "ATL-1"


def test_work_refuses_an_id(run: Runner, recorder: fake_api.Recorder, execs: Exec) -> None:
    result = run("work", fake_api.TASK_ONE_ID)

    assert result.code == 1
    assert "reference" in result.err
    assert recorder.paths() == []
    assert execs.calls == []


def test_work_on_an_unknown_task_fails_before_launching(run: Runner, execs: Exec) -> None:
    result = run("work", "ATL-99")

    assert result.code == 1
    assert "ATL-99" in result.err
    assert execs.calls == []
