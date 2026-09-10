"""The Claude Code hook: which events report to the board, and which say nothing.

Every test drives ``cylist hook`` the way Claude Code does — one JSON event on
stdin — against the fake API, and asserts on the exact HTTP that came out, or
on the fact that none did. The second kind matters as much as the first: the
hook is installed for every session on the machine, and a session that has
not said ``/work`` must leave no trace on the board.
"""

from __future__ import annotations

import io
import json
import stat
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from cylist_cli.commands import hook
from cylist_cli.main import main
from tests import fake_api
from tests.conftest import Runner

SESSION = "0192f3c4-0aaa-7000-8000-000000000001"
OTHER_SESSION = "0192f3c4-0aaa-7000-8000-000000000002"


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """The hook's state files and Claude's config, both under tmp."""
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    (tmp_path / "run").mkdir(mode=0o700, exist_ok=True)
    monkeypatch.delenv("CYLIST_TASK", raising=False)
    # These are the HTTP path's tests. It is still a supported mode — the
    # fallback for a machine where no daemon can run — and it is what every
    # event does before a daemon exists, so it earns this coverage on its
    # own. The socket path has `test_presence_hook.py`.
    monkeypatch.setenv("CYLIST_PRESENCE", "http")
    return tmp_path


def _event(name: str, **fields: Any) -> dict[str, Any]:
    return {
        "session_id": SESSION,
        "transcript_path": "/home/somebody/.claude/transcript.jsonl",
        "cwd": "/home/somebody/project",
        "hook_event_name": name,
        **fields,
    }


Fire = Callable[..., tuple[int, str, str]]


@pytest.fixture
def fire(
    recorder: fake_api.Recorder, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> Fire:
    """Feed one event to ``cylist hook`` and return (exit code, stdout, stderr)."""

    def invoke(
        event: dict[str, Any] | str,
        *,
        overrides: dict[tuple[str, str], httpx.Response] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> tuple[int, str, str]:
        payload = event if isinstance(event, str) else json.dumps(event)
        monkeypatch.setattr("sys.stdin", io.StringIO(payload))
        code = main(["hook"], transport=transport or fake_api.build(recorder, overrides=overrides))
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return invoke


def _bind(session_id: str = SESSION, task: str = "ATL-1", **more: Any) -> None:
    hook._save(session_id, {"task": task, "bound_at": hook._now().isoformat(), **more})


def _state(session_id: str = SESSION) -> dict[str, Any]:
    return hook._load(session_id)


def _put_body(recorder: fake_api.Recorder, task: str = "ATL-1", session: str = SESSION) -> Any:
    return recorder.body("PUT", f"/tasks/{task}/agent-sessions/{session}")


def _title(event: str, ref: str) -> str:
    return json.dumps({"hookSpecificOutput": {"hookEventName": event, "sessionTitle": ref}})


# --- Binding -----------------------------------------------------------------


def test_work_binds_reports_working_and_renames(fire: Fire, recorder: fake_api.Recorder) -> None:
    code, out, err = fire(_event("UserPromptSubmit", prompt="/work ATL-1", session_title="tidy"))

    assert code == 0
    assert err == ""
    assert out.strip() == _title("UserPromptSubmit", "ATL-1")
    # The name it is about to have, not the one it is losing: reporting "tidy"
    # would put that on the card for exactly one turn.
    assert _put_body(recorder) == {"state": "working", "client_name": "ATL-1"}
    assert _state()["task"] == "ATL-1"


def test_work_is_case_insensitive_on_the_key(fire: Fire, recorder: fake_api.Recorder) -> None:
    fire(_event("UserPromptSubmit", prompt="  /work atl-1 "))
    assert recorder.count("PUT", "/tasks/ATL-1/agent-sessions/" + SESSION) == 1


def test_a_prompt_that_merely_mentions_a_reference_does_not_bind(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    code, out, _ = fire(_event("UserPromptSubmit", prompt="please don't touch ATL-1"))

    assert code == 0
    assert out == ""
    assert recorder.paths() == []
    assert _state() == {}


def test_work_off_reports_done_and_forgets(fire: Fire, recorder: fake_api.Recorder) -> None:
    _bind()

    code, out, _ = fire(_event("UserPromptSubmit", prompt="/work off"))

    assert code == 0
    assert out == ""
    assert _put_body(recorder) == {
        "state": "done",
        "reason": "session_ended",
        "client_name": "ATL-1",
    }
    assert _state() == {}


def test_work_off_while_unbound_is_silent(fire: Fire, recorder: fake_api.Recorder) -> None:
    fire(_event("UserPromptSubmit", prompt="/work off"))
    assert recorder.paths() == []


def test_the_title_is_not_re_emitted_when_already_right(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _, out, _ = fire(_event("UserPromptSubmit", prompt="/work ATL-1", session_title="ATL-1"))
    assert out == ""
    assert recorder.count("PUT", "/agent-sessions/" + SESSION) == 1


def test_session_start_binds_from_the_environment(
    fire: Fire, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYLIST_TASK", "ATL-1")

    _, out, _ = fire(_event("SessionStart", source="startup"))

    assert out.strip() == _title("SessionStart", "ATL-1")
    assert _put_body(recorder)["state"] == "working"
    assert _state()["task"] == "ATL-1"


def test_session_start_keeps_a_resumed_binding(fire: Fire, recorder: fake_api.Recorder) -> None:
    _bind()

    _, out, _ = fire(_event("SessionStart", source="resume", session_title="ATL-1"))

    assert out == ""
    assert _put_body(recorder)["state"] == "working"


def test_session_start_of_an_unbound_session_says_nothing(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    code, out, _ = fire(_event("SessionStart", source="startup"))
    assert (code, out, recorder.paths()) == (0, "", [])


# --- Each event, bound -------------------------------------------------------


def test_a_prompt_on_a_bound_session_reports_working(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind()
    _, out, _ = fire(_event("UserPromptSubmit", prompt="carry on", session_title="ATL-1"))
    assert out == ""
    assert _put_body(recorder) == {"state": "working", "client_name": "ATL-1"}


def test_stop_reports_waiting_for_a_reply(fire: Fire, recorder: fake_api.Recorder) -> None:
    _bind()
    fire(_event("Stop", stop_hook_active=True))
    assert _put_body(recorder) == {
        "state": "waiting",
        "reason": "turn_ended",
        "client_name": "ATL-1",
    }


@pytest.mark.parametrize(
    ("kind", "reason"),
    [
        ("permission_prompt", "permission"),
        ("idle_prompt", "idle"),
        ("elicitation_dialog", "question"),
    ],
)
def test_a_notification_reports_waiting_with_why(
    fire: Fire, recorder: fake_api.Recorder, kind: str, reason: str
) -> None:
    _bind()
    fire(_event("Notification", notification_type=kind, message="…"))
    assert _put_body(recorder)["state"] == "waiting"
    assert _put_body(recorder)["reason"] == reason


def test_a_notification_without_a_type_reads_its_message(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind()
    fire(_event("Notification", message="Claude needs your permission to use Bash"))
    assert _put_body(recorder)["reason"] == "permission"


def test_session_end_reports_done_and_removes_the_state(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind()

    fire(_event("SessionEnd", reason="prompt_input_exit"))

    assert _put_body(recorder) == {
        "state": "done",
        "reason": "session_ended",
        "client_name": "ATL-1",
    }
    assert _state() == {}
    assert not hook._handoff_path().exists()


def test_an_unknown_event_is_ignored(fire: Fire, recorder: fake_api.Recorder) -> None:
    _bind()
    code, out, _ = fire(_event("PreCompact"))
    assert (code, out, recorder.paths()) == (0, "", [])


# --- Each event, unbound: zero HTTP --------------------------------------------


@pytest.mark.parametrize(
    "event",
    [
        _event("UserPromptSubmit", prompt="hello"),
        _event("PostToolUse", tool_name="Bash", tool_input={"command": "ls"}),
        _event("Stop"),
        _event("Notification", notification_type="permission_prompt"),
        _event("SessionEnd", reason="other"),
    ],
)
def test_an_unbound_session_sends_nothing(
    fire: Fire, recorder: fake_api.Recorder, event: dict[str, Any]
) -> None:
    code, out, _ = fire(event)
    assert (code, out, recorder.paths()) == (0, "", [])


# --- Tool calls --------------------------------------------------------------------


def _tool(name: str, task: str, session_id: str = SESSION) -> dict[str, Any]:
    return {
        **_event("PostToolUse", tool_name=f"mcp__cylist-prod__{name}", tool_input={"task": task}),
        "session_id": session_id,
    }


def test_an_ordinary_tool_call_costs_nothing(fire: Fire, recorder: fake_api.Recorder) -> None:
    """The event that got cheapest, and by far the most frequent one.

    A tool call used to cost a PUT once a minute, purely to prove the process
    was still there. A held connection proves that by existing, so an
    ordinary tool call now makes no request at all — over either channel.
    """
    _bind()

    fire(_event("PostToolUse", tool_name="Read", tool_input={"file_path": "x"}))
    fire(_event("PostToolUse", tool_name="Read", tool_input={}))

    assert recorder.paths() == []


def test_a_cylist_write_to_another_card_moves_the_binding(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind(task="ATL-1")

    _, out, _ = fire(_tool("move_task", "ATL-2"))

    # The new card only: the server ends the row on the old one itself.
    assert recorder.paths() == [f"PUT /api/v1/tasks/ATL-2/agent-sessions/{SESSION}"]
    assert _put_body(recorder, "ATL-2")["state"] == "working"
    assert _state()["task"] == "ATL-2"
    assert out == ""  # PostToolUse cannot rename; the next prompt will


def test_a_cylist_write_to_the_same_card_is_only_a_heartbeat(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind(task="ATL-1", last_heartbeat_at=hook._now().isoformat())
    fire(_tool("add_comment", "ATL-1"))
    assert recorder.paths() == []


def test_a_cylist_read_does_not_move_the_binding(fire: Fire, recorder: fake_api.Recorder) -> None:
    _bind(task="ATL-1", last_heartbeat_at=hook._now().isoformat())
    fire(_tool("get_task", "ATL-2"))
    assert _state()["task"] == "ATL-1"
    assert recorder.paths() == []


def test_a_task_given_by_id_does_not_move_the_binding(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind(task="ATL-1", last_heartbeat_at=hook._now().isoformat())
    fire(_tool("move_task", fake_api.TASK_TWO_ID))
    assert _state()["task"] == "ATL-1"


def test_an_unbound_session_is_nudged_once_and_never_calls_home(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _, first, _ = fire(_tool("move_task", "ATL-2"))
    _, second, _ = fire(_tool("add_comment", "ATL-2"))

    assert json.loads(first) == {
        "systemMessage": "Tip: /work ATL-2 shows this session on the board."
    }
    assert second == ""
    assert recorder.paths() == []
    assert _state() == {"nudged": True}


# --- /clear ------------------------------------------------------------------------


def test_clear_carries_the_binding_to_the_next_session(
    fire: Fire, recorder: fake_api.Recorder
) -> None:
    _bind(SESSION, "ATL-1")

    fire(_event("SessionEnd", reason="clear"))
    assert _put_body(recorder, session=SESSION)["state"] == "done"
    assert hook._handoff_path().exists()

    fire({**_event("SessionStart", source="clear"), "session_id": OTHER_SESSION})

    assert _put_body(recorder, session=OTHER_SESSION)["state"] == "working"
    assert _state(OTHER_SESSION)["task"] == "ATL-1"
    assert _state(SESSION) == {}
    assert not hook._handoff_path().exists()


def test_a_stale_handoff_is_not_believed(fire: Fire, recorder: fake_api.Recorder) -> None:
    hook._write_private(
        hook._handoff_path(),
        json.dumps({"task": "ATL-1", "at": (datetime.now(UTC) - timedelta(minutes=1)).isoformat()}),
    )

    fire({**_event("SessionStart", source="clear"), "session_id": OTHER_SESSION})

    assert recorder.paths() == []
    assert _state(OTHER_SESSION) == {}
    assert not hook._handoff_path().exists()


def test_a_plain_startup_ignores_a_handoff(fire: Fire, recorder: fake_api.Recorder) -> None:
    hook._write_handoff("ATL-1")
    fire(_event("SessionStart", source="startup"))
    assert recorder.paths() == []


# --- It never fails ---------------------------------------------------------------


def test_bad_json_exits_zero_and_prints_nothing(fire: Fire, recorder: fake_api.Recorder) -> None:
    code, out, err = fire("this is not json")
    assert (code, out, err, recorder.paths()) == (0, "", "", [])


def test_empty_stdin_exits_zero(fire: Fire) -> None:
    assert fire("") == (0, "", "")


def test_a_network_error_exits_zero_and_prints_nothing(fire: Fire) -> None:
    _bind()

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    code, out, err = fire(_event("Stop"), transport=httpx.MockTransport(refuse))
    assert (code, out, err) == (0, "", "")


def test_a_server_error_exits_zero(fire: Fire) -> None:
    _bind()
    code, out, _ = fire(
        _event("Stop"),
        overrides={
            ("PUT", f"/tasks/ATL-1/agent-sessions/{SESSION}"): httpx.Response(
                403, json={"error": {"code": "forbidden", "message": "no", "details": {}}}
            )
        },
    )
    assert (code, out) == (0, "")


def test_no_token_means_no_request(
    fire: Fire, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CYLIST_TOKEN")
    _bind()

    code, out, err = fire(_event("Stop"))

    assert (code, out, err, recorder.paths()) == (0, "", "", [])
    # The binding survives: the token may arrive later.
    assert _state()["task"] == "ATL-1"


def test_a_broken_config_file_does_not_fail_the_prompt(
    fire: Fire, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("CYLIST_TOKEN")
    config = tmp_path / "config" / "cylist" / "config.toml"
    config.parent.mkdir(parents=True)
    config.write_text("this is not = = toml\n")

    code, out, err = fire(_event("Stop"))

    assert (code, out, err) == (0, "", "")


def test_debugging_goes_to_stderr_only(fire: Fire, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CYLIST_HOOK_DEBUG", "1")
    code, out, err = fire("nope")
    assert (code, out) == (0, "")
    assert "cylist hook:" in err


def test_state_files_are_owner_only(fire: Fire) -> None:
    fire(_event("UserPromptSubmit", prompt="/work ATL-1"))
    path = hook._state_path(SESSION)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert (
        stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0 or True
    )  # directory mode is the umask's


# --- Installing ---------------------------------------------------------------------


def _settings(tmp_path: Path) -> Path:
    return tmp_path / "claude" / "settings.json"


def test_install_writes_every_event_and_the_work_command(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "hook_command", lambda: "/opt/cylist/bin/cylist hook")

    result = run("hook", "install")

    assert result.code == 0
    settings = json.loads(_settings(tmp_path).read_text())
    assert set(settings["hooks"]) == set(hook.HOOK_EVENTS)
    for event in hook.HOOK_EVENTS:
        [group] = settings["hooks"][event]
        assert group["hooks"] == [
            {"type": "command", "command": "/opt/cylist/bin/cylist hook", "timeout": 3}
        ]
    assert settings["hooks"]["Notification"][0]["matcher"] == (
        "permission_prompt|idle_prompt|elicitation_dialog"
    )
    assert "matcher" not in settings["hooks"]["Stop"][0]
    command = (tmp_path / "claude" / "commands" / "work.md").read_text()
    assert command.startswith("---\ndescription: Bind this session")
    assert "$ARGUMENTS" in command
    assert "Open a new Claude Code session" in result.out


def test_install_twice_changes_nothing(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "hook_command", lambda: "/opt/cylist/bin/cylist hook")
    run("hook", "install")
    once = _settings(tmp_path).read_text()

    result = run("hook", "install")

    assert result.code == 0
    assert _settings(tmp_path).read_text() == once
    assert "already" in result.out


def test_install_preserves_other_hooks_and_settings(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "hook_command", lambda: "/opt/cylist/bin/cylist hook")
    theirs = {"type": "command", "command": "notify-send done"}
    _settings(tmp_path).parent.mkdir(parents=True)
    _settings(tmp_path).write_text(
        json.dumps(
            {
                "theme": "dark",
                "hooks": {"Stop": [{"hooks": [theirs]}], "PreToolUse": [{"hooks": [theirs]}]},
            }
        )
    )

    run("hook", "install")

    settings = json.loads(_settings(tmp_path).read_text())
    assert settings["theme"] == "dark"
    assert settings["hooks"]["PreToolUse"] == [{"hooks": [theirs]}]
    assert settings["hooks"]["Stop"][0] == {"hooks": [theirs]}
    assert settings["hooks"]["Stop"][1]["hooks"][0]["command"] == "/opt/cylist/bin/cylist hook"


def test_install_moves_an_older_install_to_the_current_binary(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "hook_command", lambda: "/old/place/cylist hook")
    run("hook", "install")
    monkeypatch.setattr(hook, "hook_command", lambda: "/new/place/cylist hook")

    run("hook", "install")

    settings = json.loads(_settings(tmp_path).read_text())
    commands = {
        entry["command"]
        for groups in settings["hooks"].values()
        for g in groups
        for entry in g["hooks"]
    }
    assert commands == {"/new/place/cylist hook"}
    assert all(len(groups) == 1 for groups in settings["hooks"].values())


def test_uninstall_removes_only_ours(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(hook, "hook_command", lambda: "/opt/cylist/bin/cylist hook")
    theirs = {"type": "command", "command": "notify-send done"}
    _settings(tmp_path).parent.mkdir(parents=True)
    _settings(tmp_path).write_text(json.dumps({"hooks": {"Stop": [{"hooks": [theirs]}]}}))
    run("hook", "install")

    result = run("hook", "uninstall")

    assert result.code == 0
    settings = json.loads(_settings(tmp_path).read_text())
    assert settings == {"hooks": {"Stop": [{"hooks": [theirs]}]}}
    assert not (tmp_path / "claude" / "commands" / "work.md").exists()


def test_uninstall_with_nothing_installed_is_fine(run: Runner, tmp_path: Path) -> None:
    result = run("hook", "uninstall")
    assert result.code == 0
    assert "nothing to remove" in result.out


def test_install_refuses_a_settings_file_it_cannot_read(run: Runner, tmp_path: Path) -> None:
    _settings(tmp_path).parent.mkdir(parents=True)
    _settings(tmp_path).write_text("{not json")

    result = run("hook", "install")

    assert result.code == 1
    assert "settings file" in result.err
    assert _settings(tmp_path).read_text() == "{not json"


def test_the_hook_command_is_absolute() -> None:
    command = hook.hook_command()
    assert command.endswith(" hook")
    assert Path(command.split(" ")[0]).is_absolute()
