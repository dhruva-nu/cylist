"""``cylist skills`` — installing a project's skills where Claude Code loads them."""

from __future__ import annotations

import json
import stat
import sys
from pathlib import Path

import httpx
import pytest

from tests import fake_api
from tests.conftest import Runner

KIT_FOLDER_PATH = f"/skills/{fake_api.KIT_SKILL_ID}/folder"


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A git repository, with the command run from a directory inside it."""
    root = tmp_path / "product"
    (root / ".git").mkdir(parents=True)
    inside = root / "src" / "billing"
    inside.mkdir(parents=True)
    monkeypatch.chdir(inside)
    monkeypatch.delenv("CYLIST_TASK", raising=False)
    return root


def skills_of(root: Path) -> Path:
    return root / ".claude" / "skills"


def kit_with(**changes: object) -> dict[tuple[str, str], httpx.Response]:
    """An override answering the zip skill's folder with something else."""
    return {("GET", KIT_FOLDER_PATH): httpx.Response(200, json={**fake_api.KIT_FOLDER, **changes})}


# --- ls --------------------------------------------------------------------


def test_ls_lists_the_skills(run: Runner) -> None:
    result = run("skills", "ls", "ATL")
    assert result.code == 0
    assert "board-tidy.md" in result.out
    assert "Cut a release." in result.out


# --- pull ------------------------------------------------------------------


def test_pull_installs_every_skill_at_the_root_of_the_repository(run: Runner, repo: Path) -> None:
    result = run("skills", "pull", "ATL")

    assert result.code == 0, result.err
    skills = skills_of(repo)
    assert (skills / "board-tidy" / "SKILL.md").read_text() == fake_api.TIDY_TEXT
    kit = skills / "release-kit"
    assert (kit / "SKILL.md").read_text().endswith("Run ./scripts/cut.sh\n")
    assert (kit / "logo.png").read_bytes() == fake_api.LOGO_BYTES
    assert "installed" in result.out
    assert str(skills) in result.out


@pytest.mark.skipif(sys.platform == "win32", reason="Windows has no executable bit to set")
def test_pull_keeps_a_script_executable(run: Runner, repo: Path) -> None:
    run("skills", "pull", "ATL")

    script = skills_of(repo) / "release-kit" / "scripts" / "cut.sh"
    assert script.stat().st_mode & stat.S_IXUSR
    assert not (skills_of(repo) / "release-kit" / "SKILL.md").stat().st_mode & stat.S_IXUSR


def test_pull_marks_the_copy_as_one_git_should_ignore(run: Runner, repo: Path) -> None:
    run("skills", "pull", "ATL", "board-tidy.md")

    folder = skills_of(repo) / "board-tidy"
    assert (folder / ".gitignore").read_text().splitlines()[-1] == "*"
    marker = json.loads((folder / ".cylist-skill.json").read_text())
    assert marker["skill_id"] == fake_api.TIDY_SKILL_ID
    assert marker["project"] == "ATL"
    assert list(marker["files"]) == ["SKILL.md"]


def test_pull_takes_only_the_skills_named(
    run: Runner, repo: Path, recorder: fake_api.Recorder
) -> None:
    result = run("skills", "pull", "ATL", "release-kit")

    assert result.code == 0, result.err
    assert (skills_of(repo) / "release-kit").is_dir()
    assert not (skills_of(repo) / "board-tidy").exists()
    assert recorder.count("GET", f"/skills/{fake_api.TIDY_SKILL_ID}/folder") == 0


def test_pull_names_the_skills_there_are_when_one_is_not(run: Runner, repo: Path) -> None:
    result = run("skills", "pull", "ATL", "deploy")

    assert result.code == 1
    assert "no skill called 'deploy'" in result.err
    assert "board-tidy.md, release-kit.zip" in result.err


def test_pull_again_changes_nothing(run: Runner, repo: Path) -> None:
    run("skills", "pull", "ATL")
    written = (skills_of(repo) / "board-tidy" / "SKILL.md").stat().st_mtime_ns

    result = run("skills", "pull", "ATL")

    assert result.code == 0
    assert result.out.count("unchanged") == 2
    assert (skills_of(repo) / "board-tidy" / "SKILL.md").stat().st_mtime_ns == written


def test_pull_updates_what_it_pulled_before(run: Runner, repo: Path) -> None:
    run("skills", "pull", "ATL", "release-kit")
    newer = [fake_api.folder_file("SKILL.md", b"---\nname: release-kit\n---\nVersion two.\n")]

    result = run("skills", "pull", "ATL", "release-kit", overrides=kit_with(files=newer))

    assert result.code == 0, result.err
    assert "updated" in result.out
    kit = skills_of(repo) / "release-kit"
    assert (kit / "SKILL.md").read_text().endswith("Version two.\n")
    assert not (kit / "scripts").exists(), "a file the new version dropped goes with it"
    assert sorted(path.name for path in skills_of(repo).iterdir()) == ["release-kit"]


def test_pull_leaves_a_hand_written_skill_alone(run: Runner, repo: Path) -> None:
    mine = skills_of(repo) / "board-tidy"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("My own way of tidying.\n")

    result = run("skills", "pull", "ATL")

    assert result.code == 1
    assert "skipped: a skill that was not pulled from Cylist is already there" in result.out
    assert "--force" in result.err
    assert (mine / "SKILL.md").read_text() == "My own way of tidying.\n"
    assert (skills_of(repo) / "release-kit" / "SKILL.md").exists(), "the others still install"


def test_pull_leaves_a_pulled_skill_that_was_edited_since(run: Runner, repo: Path) -> None:
    run("skills", "pull", "ATL", "board-tidy")
    skill = skills_of(repo) / "board-tidy" / "SKILL.md"
    skill.write_text(skill.read_text() + "\nAnd one more thing I added.\n")

    result = run("skills", "pull", "ATL", "board-tidy")

    assert result.code == 1
    assert "edited since it was pulled" in result.out
    assert skill.read_text().endswith("And one more thing I added.\n")


def test_pull_force_replaces_it(run: Runner, repo: Path) -> None:
    mine = skills_of(repo) / "board-tidy"
    mine.mkdir(parents=True)
    (mine / "SKILL.md").write_text("My own way of tidying.\n")
    (mine / "notes.txt").write_text("scratch\n")

    result = run("skills", "pull", "ATL", "board-tidy", "--force")

    assert result.code == 0, result.err
    assert (mine / "SKILL.md").read_text() == fake_api.TIDY_TEXT
    assert not (mine / "notes.txt").exists()


def test_pull_refuses_a_path_that_leaves_the_folder(
    run: Runner, repo: Path, tmp_path: Path
) -> None:
    """The server refuses these too; the client does not rely on it."""
    evil = [
        fake_api.folder_file("SKILL.md", b"Fine.\n"),
        fake_api.folder_file("../../../../escaped.sh", b"rm -rf ~\n"),
    ]

    result = run("skills", "pull", "ATL", "release-kit", overrides=kit_with(files=evil))

    assert result.code == 1
    assert "not inside its folder" in result.err
    assert not list(tmp_path.rglob("escaped.sh"))
    assert not (skills_of(repo) / "release-kit").exists()


def test_pull_refuses_a_folder_name_that_is_a_path(run: Runner, repo: Path) -> None:
    result = run("skills", "pull", "ATL", "release-kit", overrides=kit_with(folder="../../x"))

    assert result.code == 1
    assert "not a skill name Claude Code accepts" in result.err


def test_pull_reports_a_skill_the_server_cannot_lay_out_and_goes_on(
    run: Runner, repo: Path
) -> None:
    refused = httpx.Response(
        422,
        json={
            "error": {
                "code": "unprocessable",
                "message": "'release-kit.zip' cannot be installed as a skill: it has no SKILL.md.",
                "details": {},
            }
        },
    )

    result = run("skills", "pull", "ATL", overrides={("GET", KIT_FOLDER_PATH): refused})

    assert result.code == 1
    assert "failed: 'release-kit.zip' cannot be installed" in result.out
    assert (skills_of(repo) / "board-tidy" / "SKILL.md").exists()


def test_pull_user_writes_to_the_claude_config_directory(
    run: Runner, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(tmp_path / "claude"))

    result = run("skills", "pull", "ATL", "board-tidy", "--user")

    assert result.code == 0, result.err
    assert (tmp_path / "claude" / "skills" / "board-tidy" / "SKILL.md").exists()
    assert not skills_of(repo).exists()


def test_pull_dest_writes_where_it_is_told(run: Runner, repo: Path, tmp_path: Path) -> None:
    result = run("skills", "pull", "ATL", "board-tidy", "--dest", str(tmp_path / "elsewhere"))

    assert result.code == 0, result.err
    assert (tmp_path / "elsewhere" / "board-tidy" / "SKILL.md").exists()


def test_pull_outside_a_repository_uses_this_directory(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    loose = tmp_path / "loose"
    loose.mkdir()
    monkeypatch.chdir(loose)

    run("skills", "pull", "ATL", "board-tidy")

    assert (loose / ".claude" / "skills" / "board-tidy" / "SKILL.md").exists()


def test_pull_takes_the_project_from_the_bound_card(
    run: Runner, repo: Path, recorder: fake_api.Recorder, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CYLIST_TASK", "atl-41")

    result = run("skills", "pull")

    assert result.code == 0, result.err
    assert "GET /api/v1/projects/ATL/skills" in recorder.paths()


def test_pull_without_a_project_says_how_to_name_one(run: Runner, repo: Path) -> None:
    result = run("skills", "pull")

    assert result.code == 1
    assert "cylist skills pull ATL" in result.err


def test_pull_json_reports_each_skill(run: Runner, repo: Path) -> None:
    result = run("--json", "skills", "pull", "ATL")

    assert result.code == 0, result.err
    report = json.loads(result.out)
    assert report["destination"] == str(skills_of(repo))
    assert [(one["folder"], one["result"]) for one in report["skills"]] == [
        ("board-tidy", "installed"),
        ("release-kit", "installed"),
    ]
    assert all(Path(one["path"]).is_absolute() for one in report["skills"])
    assert report["new_directory"] is True


def test_pull_says_when_a_running_session_will_not_see_it(run: Runner, repo: Path) -> None:
    """Claude Code watches the skills directories it found at start, and no others."""
    first = run("skills", "pull", "ATL", "board-tidy")
    second = run("skills", "pull", "ATL", "release-kit")

    assert "That directory is new" in first.out
    assert "That directory is new" not in second.out
