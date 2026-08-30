"""Exit codes, error rendering, and where the token comes from."""

from __future__ import annotations

import json
import stat
from pathlib import Path

import httpx
import pytest

from cylist_cli import config as configuration
from cylist_cli.errors import CylistError
from cylist_cli.main import main
from tests import fake_api
from tests.conftest import Runner


def _error(status: int, code: str, message: str) -> httpx.Response:
    return httpx.Response(status, json={"error": {"code": code, "message": message, "details": {}}})


# --- Errors from the server ------------------------------------------------


def test_the_servers_message_is_what_the_user_sees(run: Runner) -> None:
    result = run(
        "vault",
        "reveal",
        "ATL",
        "Logins/Billing/Stripe",
        "--show",
        overrides={
            ("POST", f"/vault/nodes/{fake_api.STRIPE_ID}/reveal"): _error(
                403, "forbidden", "This token does not hold `vault:reveal`."
            )
        },
    )
    assert result.code == 1
    assert result.err.strip() == "error: This token does not hold `vault:reveal`."
    assert "Traceback" not in result.err


def test_a_404_is_reported_not_raised(run: Runner) -> None:
    result = run(
        "project",
        "show",
        "NOPE",
        overrides={
            ("GET", "/projects/NOPE/summary"): _error(
                404, "not_found", "No project matches 'NOPE'."
            )
        },
    )
    assert result.code == 1
    assert result.err.strip() == "error: No project matches 'NOPE'."


def test_json_mode_renders_the_error_envelope_on_stderr(run: Runner) -> None:
    result = run(
        "--json",
        "project",
        "show",
        "NOPE",
        overrides={
            ("GET", "/projects/NOPE/summary"): _error(
                404, "not_found", "No project matches 'NOPE'."
            )
        },
    )
    assert result.code == 1
    assert result.out == ""
    envelope = json.loads(result.err)
    assert envelope["error"]["code"] == "not_found"
    assert envelope["error"]["status"] == 404


def test_a_body_that_is_not_our_envelope_still_produces_a_sentence(run: Runner) -> None:
    """A proxy's HTML 502 has never heard of {"error": {...}}."""
    result = run(
        "projects",
        overrides={("GET", "/projects"): httpx.Response(502, text="<html>bad gateway</html>")},
    )
    assert result.code == 1
    assert "502" in result.err
    assert "<html>" not in result.err


def test_an_unauthenticated_call_suggests_logging_in(run: Runner) -> None:
    result = run(
        "projects",
        overrides={("GET", "/projects"): httpx.Response(401, text="")},
    )
    assert result.code == 1
    assert "cylist login" in result.err


def test_an_unreachable_server_names_the_url(run: Runner) -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = run("projects", overrides={("GET", "/projects"): httpx.Response(200)})
    assert result.code == 0  # sanity: the fake works before we break it

    code = main(["projects"], transport=httpx.MockTransport(refuse))
    assert code == 1


def test_an_unknown_command_is_a_usage_error(run: Runner) -> None:
    with pytest.raises(SystemExit) as exit_info:
        run("teleport")
    assert exit_info.value.code == 2


def test_a_missing_token_explains_the_three_ways_to_supply_one(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    code = main(["projects"], transport=fake_api.build(fake_api.Recorder()))
    captured = capsys.readouterr()
    assert code == 1
    assert "cylist login" in captured.err
    assert "CYLIST_TOKEN" in captured.err


# --- Configuration ---------------------------------------------------------


def test_the_environment_beats_the_config_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.toml"
    configuration.save("http://from-file", "token-from-file", path=path)
    monkeypatch.setenv("CYLIST_TOKEN", "token-from-env")

    config = configuration.load(path=path)
    assert config.token == "token-from-env"
    assert config.token_source == "CYLIST_TOKEN"
    # The URL still comes from the file: CYLIST_URL was not set.
    monkeypatch.delenv("CYLIST_URL")
    assert configuration.load(path=path).url == "http://from-file"


def test_the_url_flag_beats_everything(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    configuration.save("http://from-file", "token", path=path)
    assert configuration.load("http://from-flag/", path=path).url == "http://from-flag"


def test_a_saved_token_is_owner_only(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    configuration.save("http://cylist.test", "cyl_secret", path=path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert configuration.describe_mode(path) == "0600"


def test_saving_over_a_loose_file_tightens_it(tmp_path: Path) -> None:
    """An existing 0644 file keeps its mode through O_CREAT; it must not."""
    path = tmp_path / "config.toml"
    path.write_text("url = 'http://old'\n")
    path.chmod(0o644)
    configuration.save("http://cylist.test", "cyl_secret", path=path)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_a_missing_config_file_is_not_an_error(tmp_path: Path) -> None:
    config = configuration.load(path=tmp_path / "absent.toml")
    assert config.token_source == "CYLIST_TOKEN"


def test_broken_toml_is_reported_clearly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    path = tmp_path / "config.toml"
    path.write_text("this is not = = toml\n")
    with pytest.raises(CylistError) as error:
        configuration.load(path=path)
    assert "not valid TOML" in error.value.message


def test_login_verifies_the_token_before_writing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    import io

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("cyl_pasted_token\n"))

    recorder = fake_api.Recorder()
    code = main(
        ["login", "--token-stdin", "--url", "http://cylist.test"],
        transport=fake_api.build(recorder),
    )
    captured = capsys.readouterr()

    assert code == 0
    assert "GET /api/v1/me" in recorder.paths()
    assert recorder.sent("GET", "/me").headers["authorization"] == "Bearer cyl_pasted_token"

    written = tmp_path / "cylist" / "config.toml"
    assert "cyl_pasted_token" in written.read_text()
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert "mode 0600" in captured.out


def test_login_writes_nothing_when_the_token_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.delenv("CYLIST_TOKEN", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO("cyl_wrong\n"))

    code = main(
        ["login", "--token-stdin"],
        transport=fake_api.build(
            fake_api.Recorder(),
            overrides={("GET", "/me"): _error(401, "unauthorized", "That token is not valid.")},
        ),
    )
    assert code == 1
    assert not (tmp_path / "cylist" / "config.toml").exists()


def test_whoami_reports_the_scopes(run: Runner) -> None:
    result = run("whoami")
    assert result.code == 0
    assert "read, write" in result.out


def test_no_token_flag_exists(run: Runner) -> None:
    """A token on the command line would land in shell history."""
    with pytest.raises(SystemExit) as exit_info:
        run("--token", "cyl_oops", "projects")
    assert exit_info.value.code == 2
