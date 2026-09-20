"""``cylist setup`` — the one command, and what it leaves behind."""

from __future__ import annotations

import getpass
import json
import shutil
import sys
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

import httpx
import pytest

from cylist_cli import config as configuration
from cylist_cli import system
from cylist_cli.commands import hook, setup
from cylist_cli.main import main
from tests import fake_api
from tests.conftest import Runner, posix_modes_only


@pytest.fixture(autouse=True)
def claude_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A Claude Code configuration directory of our own to write into."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))


@pytest.fixture
def claude_calls(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Every ``claude mcp …`` the command ran, instead of running it."""
    calls: list[list[str]] = []

    def fake_runner(argv: Sequence[str]) -> tuple[int, str]:
        calls.append(list(argv))
        return 0, ""

    monkeypatch.setattr(setup, "_runner", fake_runner)
    monkeypatch.setattr(shutil, "which", _which)
    return calls


def _stored() -> dict[str, Any]:
    """What the config file says, read directly.

    Not through :func:`cylist_cli.config.load`: the fixtures put a
    ``CYLIST_URL`` in the environment, which by design overrides the file — so
    going through ``load`` here would assert on the environment instead of on
    what this command wrote.
    """
    return tomllib.loads(configuration.config_path().read_text("utf-8"))


def _which(name: str) -> str | None:
    """A PATH holding ``claude`` and ``uv`` but not ``cylist-mcp``."""
    return {"claude": "/usr/bin/claude", "uv": "/usr/bin/uv"}.get(name)


@pytest.fixture
def fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    """No token configured, which is what a first run looks like."""
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)


@pytest.fixture
def credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Answer both prompts: the address to sign in as, and the password.

    Two now rather than one. A deployment with accounts in it signs people in
    by address, and setup has to ask for one.
    """
    monkeypatch.setattr("builtins.input", lambda _: EMAIL)
    monkeypatch.setattr(getpass, "getpass", lambda _: "correct-horse")


EMAIL = "dhruva@cylist.dev"


def test_a_first_run_mints_a_token_and_writes_it(
    run: Runner,
    recorder: fake_api.Recorder,
    fresh: None,
    credentials: None,
    claude_calls: list[list[str]],
) -> None:
    result = run("setup")

    assert result.code == 0
    assert recorder.body("POST", "/auth/login") == {
        "email": EMAIL,
        "password": "correct-horse",
    }
    minted = recorder.body("POST", "/tokens")
    assert minted["scopes"] == ["read", "write"]
    assert minted["name"].endswith("agent")

    assert _stored()["token"] == fake_api.MINTED_TOKEN


def test_the_login_carries_no_bearer_header(
    run: Runner, recorder: fake_api.Recorder, fresh: None, credentials: None
) -> None:
    """The server reads Authorization in preference to the session cookie, so
    a stale bearer on the login request would refuse the very call that is
    trying to get a session."""
    run("setup", "--no-mcp")

    assert "authorization" not in recorder.sent("POST", "/auth/login").headers
    assert "authorization" not in recorder.sent("POST", "/tokens").headers


def test_the_session_it_borrows_is_revoked_again(
    run: Runner, recorder: fake_api.Recorder, fresh: None, credentials: None
) -> None:
    """The password buys every scope; nothing should keep that lying around."""
    run("setup", "--no-mcp")

    assert recorder.count("POST", "/auth/logout") == 1


def test_every_address_the_server_answers_on_is_stored(
    run: Runner, fresh: None, credentials: None
) -> None:
    """The whole point: this configuration works on and off the tailnet."""
    run("setup", "--no-mcp")

    assert _stored()["urls"] == ["http://cylist.test", fake_api.TAILNET_URL]


@posix_modes_only
def test_the_config_file_is_owner_only(run: Runner, fresh: None, credentials: None) -> None:
    run("setup", "--no-mcp")

    assert configuration.describe_mode(configuration.config_path()) == "0600"


def test_the_config_file_is_described_as_private_on_every_platform(
    run: Runner, fresh: None, credentials: None
) -> None:
    """What Windows gives instead of a mode is the ACL on the user's profile,
    and `describe_protection` is what says so rather than claiming a 0600 that
    `os.chmod` there never set."""
    result = run("setup", "--no-mcp")

    assert configuration.describe_protection(configuration.config_path()) in result.out


def test_a_working_token_is_kept_and_no_password_is_asked_for(
    run: Runner, recorder: fake_api.Recorder, claude_calls: list[list[str]]
) -> None:
    """A re-run — after a reinstall, say — must not need the password again.

    ``CYLIST_TOKEN`` is set by the test fixtures, and the fake API says it
    holds read and write, so there is nothing to mint.
    """
    result = run("setup")

    assert result.code == 0
    assert recorder.count("POST", "/auth/login") == 0
    assert "Kept the token already configured" in result.out


def test_a_token_without_the_scopes_an_agent_needs_is_replaced(
    run: Runner, recorder: fake_api.Recorder, credentials: None
) -> None:
    reader = {**fake_api.IDENTITY, "scopes": ["read"]}
    result = run("setup", "--no-mcp", overrides={("GET", "/me"): httpx.Response(200, json=reader)})

    assert result.code == 0
    assert recorder.count("POST", "/tokens") == 1


def test_a_rejected_token_is_replaced_rather_than_reported(
    run: Runner, recorder: fake_api.Recorder, credentials: None
) -> None:
    """A revoked token is the reason to run this, not a reason to fail."""
    refusal = httpx.Response(
        401, json={"error": {"code": "unauthorized", "message": "No.", "details": {}}}
    )
    result = run("setup", "--no-mcp", overrides={("GET", "/me"): refusal})

    assert result.code == 0
    assert recorder.count("POST", "/tokens") == 1


def test_the_hooks_and_the_work_command_are_installed(
    run: Runner, fresh: None, credentials: None
) -> None:
    run("setup", "--no-mcp")

    settings = json.loads((Path(hook.claude_config_dir()) / "settings.json").read_text())
    assert set(settings["hooks"]) == set(hook.HOOK_EVENTS)
    assert (hook.claude_config_dir() / "commands" / "work.md").is_file()


def test_the_mcp_server_is_registered_without_a_token_in_it(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    """A registration holding a live credential is how tokens reach a repo."""
    result = run("setup")

    assert result.code == 0
    add = next(call for call in claude_calls if call[2] == "add")
    assert add[:4] == ["/usr/bin/claude", "mcp", "add", "cylist"]
    assert add[4:6] == ["--scope", "user"]
    assert add[6] == "--"
    # The command it will run, and nothing else: no -e, no --env, no token.
    # Resolved rather than spelled out: `Path("/usr/bin/uv").resolve()` is
    # `D:\\usr\\bin\\uv` on Windows, and which uv it is is not the point here.
    assert add[7] == str(Path("/usr/bin/uv").resolve())
    assert not any(part.startswith("-e") or part.startswith("--env") for part in add)
    assert fake_api.MINTED_TOKEN not in " ".join(add)


def test_registering_replaces_whatever_was_there_before(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    """``add-json`` refuses a name it knows, and this command must be re-runnable."""
    run("setup")

    assert [call[2] for call in claude_calls] == ["remove", "add"]


def test_the_scope_can_be_narrowed_to_this_project(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    run("setup", "--scope", "project")

    for call in claude_calls:
        assert "project" in call
        assert "user" not in call


def test_a_failure_from_claude_is_reported_not_swallowed(
    run: Runner,
    fresh: None,
    credentials: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(shutil, "which", _which)
    monkeypatch.setattr(setup, "_runner", lambda argv: (1, "no such scope"))

    result = run("setup")

    # Still a success: the token and the hooks are in place, and only the
    # registration needs a hand. Exiting 1 would suggest undoing the rest.
    assert result.code == 0
    assert "no such scope" in result.out


def test_no_claude_on_path_prints_the_command_to_run(
    run: Runner, fresh: None, credentials: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(shutil, "which", lambda name: {"uv": "/usr/bin/uv"}.get(name))

    result = run("setup")

    assert result.code == 0
    assert "mcp add cylist" in result.out


def test_hooks_and_mcp_can_both_be_declined(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    result = run("setup", "--no-hooks", "--no-mcp")

    assert result.code == 0
    assert claude_calls == []
    assert not (hook.claude_config_dir() / "settings.json").exists()
    assert _stored()["token"] == fake_api.MINTED_TOKEN


def test_an_empty_password_changes_nothing(
    run: Runner, recorder: fake_api.Recorder, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("builtins.input", lambda _: EMAIL)
    monkeypatch.setattr(getpass, "getpass", lambda _: "")

    result = run("setup")

    assert result.code == 1
    assert "nothing was changed" in result.err
    assert recorder.count("POST", "/auth/login") == 0
    assert not configuration.config_path().exists()


def test_the_password_can_come_from_stdin(
    run: Runner,
    recorder: fake_api.Recorder,
    fresh: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "stdin", _Stdin("hunter2\n"))

    result = run("setup", "--no-mcp", "--password-stdin", "--email", EMAIL)

    assert result.code == 0
    assert recorder.body("POST", "/auth/login") == {"email": EMAIL, "password": "hunter2"}


def test_stdin_without_an_email_says_so_rather_than_hanging(
    run: Runner, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is nothing to prompt with: stdin is already the password."""
    monkeypatch.setattr(sys, "stdin", _Stdin("hunter2\n"))

    result = run("setup", "--no-mcp", "--password-stdin")

    assert result.code == 1
    assert "--email" in result.err


def test_a_deployment_with_no_accounts_is_not_asked_for_an_email(
    run: Runner, recorder: fake_api.Recorder, fresh: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bootstrap login belongs to nobody, so there is no address to give.

    ``input`` is left unpatched on purpose: reading it under pytest raises,
    so a regression that starts prompting here fails rather than hangs.
    """
    monkeypatch.setattr(getpass, "getpass", lambda _: "correct-horse")

    result = run(
        "setup",
        "--no-mcp",
        overrides={
            ("GET", "/setup"): httpx.Response(200, json={**fake_api.SETUP, "has_accounts": False})
        },
    )

    assert result.code == 0
    assert recorder.body("POST", "/auth/login") == {"password": "correct-horse"}


def test_a_server_too_old_to_describe_itself_still_works(
    run: Runner, fresh: None, credentials: None
) -> None:
    """During a rollout the CLI is new and the deployment is not yet."""
    result = run(
        "setup",
        "--no-mcp",
        overrides={
            ("GET", "/setup"): httpx.Response(
                404, json={"error": {"code": "not_found", "message": "No.", "details": {}}}
            )
        },
    )

    assert result.code == 0
    assert _stored()["urls"] == ["http://cylist.test"]
    assert _stored()["token"] == fake_api.MINTED_TOKEN


def test_a_server_that_cannot_be_found_says_what_it_tried(
    fresh: None, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    code = main(["setup"], transport=httpx.MockTransport(refuse))
    reported = capsys.readouterr().err

    assert code == 1
    assert "Cannot find a Cylist server" in reported
    assert "cylist.test" in reported


def test_json_reports_every_step(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    result = run("--json", "setup")

    reported = json.loads(result.out)
    assert reported["url"] == "http://cylist.test"
    assert reported["urls"] == ["http://cylist.test", fake_api.TAILNET_URL]
    assert reported["token"]["minted"] is True
    assert reported["mcp"]["registered"] is True
    assert reported["hooks"]["added"] == list(hook.HOOK_EVENTS)


class _Stdin:
    """Just enough of ``sys.stdin`` for ``--password-stdin``."""

    def __init__(self, text: str) -> None:
        self._text = text

    def readline(self) -> str:
        return self._text


# --- Windows ---------------------------------------------------------------
#
# Every test here fakes the platform at call time, the way ``as_windows`` in
# test_presence_portability.py does, so the branches a Windows user takes are
# covered from a Linux runner. What that module already proves about the
# daemon is not repeated; what is left is what *setup* owns.


@pytest.fixture
def as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")


def test_a_batch_shim_is_run_through_the_command_processor(
    as_windows: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code installs as ``claude.cmd``, and ``CreateProcess`` cannot
    start a batch file — the error says only "not a valid Win32 application",
    which names neither batch files nor Claude."""
    monkeypatch.setenv("COMSPEC", "C:\\Windows\\system32\\cmd.exe")

    assert setup._executable(["C:\\npm\\claude.cmd", "mcp", "add"]) == [
        "C:\\Windows\\system32\\cmd.exe",
        "/c",
        "C:\\npm\\claude.cmd",
        "mcp",
        "add",
    ]


def test_an_exe_is_run_directly(as_windows: None) -> None:
    argv = ["C:\\Program Files\\claude\\claude.exe", "mcp", "add"]

    assert setup._executable(argv) == argv


def test_nothing_is_wrapped_on_posix() -> None:
    assert setup._executable(["/usr/bin/claude", "mcp"]) == ["/usr/bin/claude", "mcp"]


def test_the_registration_carries_no_json_to_be_mangled() -> None:
    """``claude mcp add`` takes the command as arguments. ``add-json`` would
    put a document full of quotes through a batch shim and the command
    processor, and trust both to hand it over intact."""
    argv = setup._add_argv(
        "C:\\npm\\claude.cmd",
        ("C:\\uv\\uv.exe", "--directory", "C:\\Program Files\\cylist\\mcp", "run"),
        "user",
    )

    assert "--" in argv
    assert not any('"' in part or "{" in part for part in argv)
    assert argv[-2:] == ["C:\\Program Files\\cylist\\mcp", "run"]


def test_a_windows_path_with_a_space_is_quoted_when_shown(as_windows: None) -> None:
    """The ``claude mcp add`` line setup prints when it cannot run one itself
    has to be pasteable: ``C:\\Program Files\\…`` is otherwise two arguments."""
    rendered = system.shell_command(["C:\\Program Files\\cylist\\cylist.exe", "hook"])

    assert rendered == '"C:\\Program Files\\cylist\\cylist.exe" hook'


def test_a_posix_path_is_left_alone() -> None:
    assert system.shell_command(["/usr/local/bin/cylist", "hook"]) == "/usr/local/bin/cylist hook"


def test_state_lives_in_one_place_whatever_the_platform(as_windows: None) -> None:
    """One rule for every machine-local file, so a Windows install does not
    keep its sessions under ``%LOCALAPPDATA%`` and the address that last
    answered under ``~/.local/state``."""
    assert hook.sessions_dir().parent == system.state_dir()
    assert system.state_dir().name == "cylist"


# --- Installing the MCP server ---------------------------------------------
#
# The case a laptop joining a board is actually in: the CLI came from git, so
# there is no `mcp` directory anywhere and nothing called `cylist-mcp` on
# PATH. This used to end with "could not find the MCP server".


_MCP_BINARY = "cylist-mcp.exe" if system.windows() else "cylist-mcp"
"""What `uv tool install` leaves behind here — and what `_installed_binary`
goes looking for, which is the thing these tests are about."""


@pytest.fixture
def no_checkout(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ``mcp`` directory to be found — an installed CLI, not a clone."""
    monkeypatch.setattr(setup, "_mcp_dir", lambda _: None)


@pytest.fixture
def uv_calls(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """A ``uv`` that installs into a bin directory of our own, and a PATH that
    does not have ``cylist-mcp`` on it even after that — which is the real
    shape of the problem: uv's bin directory is on the *user's* PATH, not on
    this process's."""
    calls: list[list[str]] = []
    binaries = tmp_path / "uv-bin"
    binaries.mkdir()

    def fake_runner(argv: Sequence[str], timeout: int = 0) -> tuple[int, str]:
        calls.append(list(argv))
        if argv[1:3] == ["tool", "install"]:
            (binaries / _MCP_BINARY).write_text("#!/bin/sh\n")
            return 0, ""
        if argv[1:4] == ["tool", "dir", "--bin"]:
            return 0, f"{binaries}\n"
        return 0, ""

    monkeypatch.setattr(setup, "_runner", fake_runner)
    monkeypatch.setattr(shutil, "which", _which)
    return calls


def test_a_machine_with_no_checkout_gets_the_server_installed(
    run: Runner, fresh: None, credentials: None, no_checkout: None, uv_calls: list[list[str]]
) -> None:
    result = run("--json", "setup")

    reported = json.loads(result.out)["mcp"]
    assert reported["installed"] is True
    assert reported["registered"] is True
    assert reported["command"][0].endswith(_MCP_BINARY)

    installs = [call for call in uv_calls if call[1:3] == ["tool", "install"]]
    assert installs == [["/usr/bin/uv", "tool", "install", "--force", setup.MCP_SOURCE]]


def test_the_installed_binary_is_found_by_asking_uv_not_by_searching_path(
    run: Runner, fresh: None, credentials: None, no_checkout: None, uv_calls: list[list[str]]
) -> None:
    """``shutil.which`` cannot see it: uv installs into a directory that is on
    the user's PATH and very often not on this process's — a shell opened
    before uv was. Registering a bare name that this process cannot resolve
    would produce an MCP server that never starts."""
    run("setup")

    assert ["/usr/bin/uv", "tool", "dir", "--bin"] in uv_calls
    assert shutil.which("cylist-mcp") is None


def test_the_source_can_be_pointed_at_a_fork(
    run: Runner, fresh: None, credentials: None, no_checkout: None, uv_calls: list[list[str]]
) -> None:
    run("setup", "--mcp-source", "git+https://example.test/fork#subdirectory=mcp")

    installs = [call for call in uv_calls if call[1:3] == ["tool", "install"]]
    assert installs[0][-1] == "git+https://example.test/fork#subdirectory=mcp"


def test_the_bootstrap_scripts_can_set_the_source_in_the_environment(
    run: Runner,
    fresh: None,
    credentials: None,
    no_checkout: None,
    uv_calls: list[list[str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """So that a machine bootstrapped from one branch does not then fetch its
    MCP server from another."""
    monkeypatch.setenv(setup.MCP_SOURCE_ENV, "git+https://example.test/c@staging#subdirectory=mcp")

    run("setup")

    installs = [call for call in uv_calls if call[1:3] == ["tool", "install"]]
    assert installs[0][-1] == "git+https://example.test/c@staging#subdirectory=mcp"


def test_a_checkout_is_still_preferred_to_a_download(
    run: Runner, fresh: None, credentials: None, claude_calls: list[list[str]]
) -> None:
    """Nothing is fetched when the source is right there — which is what keeps
    this command usable on the machine the server runs on."""
    result = run("--json", "setup")

    reported = json.loads(result.out)["mcp"]
    assert reported["installed"] is False
    assert not any(call[1:3] == ["tool", "install"] for call in claude_calls)


def test_no_install_prints_the_command_instead_of_running_it(
    run: Runner,
    fresh: None,
    credentials: None,
    no_checkout: None,
    uv_calls: list[list[str]],
) -> None:
    result = run("setup", "--no-install")

    assert not any(call[1:3] == ["tool", "install"] for call in uv_calls)
    assert "uv tool install" in result.out
    assert setup.MCP_SOURCE in result.out


def test_no_uv_says_to_install_uv_rather_than_naming_a_uv_command(
    run: Runner, fresh: None, credentials: None, no_checkout: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Install it with uv tool install" is not useful advice to a machine
    that has no uv."""
    monkeypatch.setattr(shutil, "which", lambda name: {"claude": "/usr/bin/claude"}.get(name))

    result = run("setup")

    assert "uv is not on PATH" in result.out
    assert "docs.astral.sh/uv" in result.out


def test_a_failed_install_names_the_source_it_could_not_reach(
    run: Runner,
    fresh: None,
    credentials: None,
    no_checkout: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(argv: Sequence[str], timeout: int = 0) -> tuple[int, str]:
        return (1, "Could not resolve host") if argv[1:3] == ["tool", "install"] else (0, "")

    monkeypatch.setattr(setup, "_runner", failing)
    monkeypatch.setattr(shutil, "which", _which)

    result = run("setup")

    assert setup.MCP_SOURCE in result.out
    assert "--mcp-dir" in result.out


def test_the_token_is_still_written_when_the_mcp_server_cannot_be_had(
    run: Runner,
    fresh: None,
    credentials: None,
    no_checkout: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The MCP server is the last step and the least of them. A machine that
    cannot fetch one is still set up to run `cylist` and report presence."""
    monkeypatch.setattr(setup, "_runner", lambda argv, timeout=0: (1, "no network"))
    monkeypatch.setattr(shutil, "which", _which)

    result = run("setup")

    assert result.code == 0
    assert _stored()["token"].startswith("cyl_")
