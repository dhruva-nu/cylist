"""``cylist repo setup``: what lands in a repository's CLAUDE.md, and what never does.

Three properties carry this command, and most of what is below is one of
them: it writes nothing until every request has answered, it never touches a
byte outside its own fence, and two runs against an unchanged board leave the
file identical. The fourth — that it behaves the same on Windows — is here as
well, as far as a Linux run can take it: the line-ending contract is asserted
directly, and the rest is `mypy --platform win32` in CI.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx
import pytest

from cylist_cli.commands import repo
from tests import fake_api
from tests.conftest import Runner

MISSING = httpx.Response(
    404, json={"error": {"code": "not_found", "message": "No project 'NOPE'.", "details": {}}}
)


def _claude_md(directory: Path) -> Path:
    return directory / "CLAUDE.md"


# --- What it writes ---------------------------------------------------------


def test_a_fresh_repository_gets_the_general_half(run: Runner, tmp_path: Path) -> None:
    """Every tool an agent needs to be told about is named, by the name it has."""
    result = run("repo", "setup", "ATL", "--path", str(tmp_path))

    assert result.code == 0
    text = _claude_md(tmp_path).read_text("utf-8")
    assert text.startswith("<!-- cylist:begin ATL -->\n")
    assert text.endswith("<!-- cylist:end ATL -->\n")
    for tool in (
        "ask_docs",
        "place_doc",
        "write_doc",
        "list_docs",
        "read_doc",
        "list_skills",
        "read_skill",
        "download_skill",
        "list_vault",
        "reveal_secret",
        "add_secret",
    ):
        assert f"`{tool}`" in text, f"{tool} is not mentioned"
    assert "not a progress log" in text.lower()
    assert "There are no credentials in this repository" in text


def test_a_fresh_repository_gets_the_half_read_off_the_board(
    run: Runner, tmp_path: Path, recorder: fake_api.Recorder
) -> None:
    """The project's own columns, docs and skills, not a description from memory."""
    result = run("repo", "setup", "ATL", "--path", str(tmp_path))

    assert result.code == 0
    text = _claude_md(tmp_path).read_text("utf-8")
    assert "**ATL — Atlas migration**" in text
    assert "Moving Atlas off the legacy billing stack." in text
    # Column names exactly as move_task wants them, each with what it means.
    assert "| `Backlog` | Not started. |" in text
    assert "| `In progress` | Being worked on. |" in text
    assert "| `Done` | Finished. |" in text
    # The shape of the docs: topic and how many docs are in it, by section.
    assert "- **Product** — Billing (1)" in text
    assert "- **Engineering** — Architecture (2)" in text
    # And the skills.
    assert "| `board-tidy.md` | Move stale cards back to triage. |" in text
    assert "| `release-kit.zip` | Cut a release. |" in text
    assert recorder.count("GET", "/projects/ATL/columns") == 1
    assert recorder.count("GET", "/projects/ATL/docs") == 1
    assert recorder.count("GET", "/projects/ATL/skills") == 1
    assert "Created" in result.out


def test_a_column_name_is_copied_character_for_character(run: Runner, tmp_path: Path) -> None:
    """Boards really do have a column called 'Dev ', and move_task is literal.

    Collapsing that trailing space would give every session afterwards a name
    the API refuses, which is the one mistake this table exists to prevent.
    """
    odd = httpx.Response(
        200,
        json={
            "columns": [
                {
                    "id": fake_api.BACKLOG_ID,
                    "project_id": fake_api.PROJECT_ID,
                    "name": "Dev ",
                    "description": "can be deployed in dev",
                    "position": 0,
                    "task_count": 0,
                }
            ],
            "min_columns": 2,
            "max_columns": 8,
        },
    )

    result = run(
        "repo",
        "setup",
        "ATL",
        "--path",
        str(tmp_path),
        overrides={("GET", "/projects/ATL/columns"): odd},
    )

    assert result.code == 0
    assert "| `Dev ` | can be deployed in dev |" in _claude_md(tmp_path).read_text("utf-8")


def test_a_description_that_stops_short_is_given_its_full_stop(run: Runner, tmp_path: Path) -> None:
    """Project descriptions are typed into a form; most of them have no full stop."""
    blunt = httpx.Response(
        200, json={**fake_api.SUMMARY, "description": "Manage personal project with ease"}
    )

    result = run(
        "repo",
        "setup",
        "ATL",
        "--path",
        str(tmp_path),
        overrides={("GET", "/projects/ATL/summary"): blunt},
    )

    assert result.code == 0
    text = _claude_md(tmp_path).read_text("utf-8")
    assert "**ATL — Atlas migration**. Manage personal project with ease." in text


def test_a_project_with_nothing_written_down_says_so(run: Runner, tmp_path: Path) -> None:
    """An empty table would read as 'there is nothing to find here'. There is."""
    empty_tree = httpx.Response(
        200,
        json={
            "sections": [
                {"section": "product", "label": "Product", "topics": []},
                {"section": "engineering", "label": "Engineering", "topics": []},
            ],
            "doc_count": 0,
        },
    )
    result = run(
        "repo",
        "setup",
        "ATL",
        "--path",
        str(tmp_path),
        overrides={
            ("GET", "/projects/ATL/docs"): empty_tree,
            ("GET", "/projects/ATL/skills"): httpx.Response(200, json=[]),
        },
    )

    assert result.code == 0
    text = _claude_md(tmp_path).read_text("utf-8")
    assert "ATL has no docs yet." in text
    assert "ATL has no skills yet" in text
    assert "| --- |" in text  # the columns table is still there


# --- What it leaves alone ---------------------------------------------------


def test_it_appends_and_leaves_what_was_there_alone(run: Runner, tmp_path: Path) -> None:
    path = _claude_md(tmp_path)
    path.write_text("# Atlas\n\nRun the tests with `make test`.\n", encoding="utf-8")

    result = run("repo", "setup", "ATL", "--path", str(path))

    assert result.code == 0
    text = path.read_text("utf-8")
    assert text.startswith(
        "# Atlas\n\nRun the tests with `make test`.\n\n<!-- cylist:begin ATL -->\n"
    )
    assert "Added ATL's Cylist block" in result.out


def test_a_second_run_against_an_unchanged_board_changes_no_byte(
    run: Runner, tmp_path: Path
) -> None:
    run("repo", "setup", "ATL", "--path", str(tmp_path))
    once = _claude_md(tmp_path).read_bytes()

    result = run("repo", "setup", "ATL", "--path", str(tmp_path))

    assert result.code == 0
    assert _claude_md(tmp_path).read_bytes() == once
    assert once.count(b"cylist:begin ATL") == 1
    assert "already says this" in result.out


def test_a_moved_board_replaces_the_block_rather_than_adding_a_second(
    run: Runner, tmp_path: Path
) -> None:
    path = _claude_md(tmp_path)
    path.write_text("# Atlas\n", encoding="utf-8")
    run("repo", "setup", "ATL", "--path", str(path))
    renamed = httpx.Response(
        200,
        json={
            "columns": [
                {
                    "id": fake_api.BACKLOG_ID,
                    "project_id": fake_api.PROJECT_ID,
                    "name": "Triage",
                    "description": "Not looked at yet.",
                    "position": 0,
                    "task_count": 1,
                }
            ],
            "min_columns": 2,
            "max_columns": 8,
        },
    )

    result = run(
        "repo",
        "setup",
        "ATL",
        "--path",
        str(path),
        overrides={("GET", "/projects/ATL/columns"): renamed},
    )

    assert result.code == 0
    text = path.read_text("utf-8")
    assert text.count("cylist:begin ATL") == 1
    assert "| `Triage` |" in text
    assert "| `In progress` |" not in text
    assert text.startswith("# Atlas\n\n<!-- cylist:begin ATL -->\n")
    assert "Updated ATL's Cylist block" in result.out


def test_a_second_project_gets_a_block_of_its_own(run: Runner, tmp_path: Path) -> None:
    """One repository, two boards: the fence carries the key so neither wins."""
    run("repo", "setup", "ATL", "--path", str(tmp_path))
    billing = httpx.Response(200, json={**fake_api.SUMMARY, "key": "BIL", "name": "Billing"})

    result = run(
        "repo",
        "setup",
        "BIL",
        "--path",
        str(tmp_path),
        overrides={("GET", "/projects/BIL/summary"): billing},
    )

    assert result.code == 0
    text = _claude_md(tmp_path).read_text("utf-8")
    assert text.index("cylist:begin ATL") < text.index("cylist:begin BIL")
    assert text.count("cylist:end ") == 2


def test_a_fence_with_no_end_is_refused_rather_than_guessed_at(run: Runner, tmp_path: Path) -> None:
    """Half a block means somebody deleted a line. Writing over it would eat the rest."""
    path = _claude_md(tmp_path)
    damaged = "# Atlas\n\n<!-- cylist:begin ATL -->\nhalf a block\n\n## My own notes\n"
    path.write_text(damaged, encoding="utf-8")

    result = run("repo", "setup", "ATL", "--path", str(path))

    assert result.code == 1
    assert "with no '<!-- cylist:end ATL -->' after it" in result.err
    assert path.read_text("utf-8") == damaged


# --- What it does before it writes ------------------------------------------


def test_a_key_that_is_not_there_writes_nothing_and_names_the_ones_that_are(
    run: Runner, tmp_path: Path, recorder: fake_api.Recorder
) -> None:
    result = run(
        "repo",
        "setup",
        "NOPE",
        "--path",
        str(tmp_path),
        overrides={("GET", "/projects/NOPE/summary"): MISSING},
    )

    assert result.code == 1
    assert "error: No project 'NOPE' on this server, so nothing was written." in result.err
    assert "It has: ATL." in result.err
    assert not _claude_md(tmp_path).exists()
    assert recorder.count("GET", "/projects/NOPE/columns") == 0
    assert recorder.count("GET", "/projects/NOPE/docs") == 0


def test_a_failure_part_way_through_leaves_no_half_written_file(
    run: Runner, tmp_path: Path
) -> None:
    """Every request is made before the file is opened, so the last one can still refuse."""
    refused = httpx.Response(
        403, json={"error": {"code": "forbidden", "message": "Not your board.", "details": {}}}
    )

    result = run(
        "repo",
        "setup",
        "ATL",
        "--path",
        str(tmp_path),
        overrides={("GET", "/projects/ATL/skills"): refused},
    )

    assert result.code == 1
    assert "error: Not your board." in result.err
    assert not _claude_md(tmp_path).exists()


def test_dry_run_prints_the_block_and_writes_nothing(run: Runner, tmp_path: Path) -> None:
    result = run("repo", "setup", "ATL", "--path", str(tmp_path), "--dry-run")

    assert result.code == 0
    assert not _claude_md(tmp_path).exists()
    assert result.out.startswith("<!-- cylist:begin ATL -->\n")
    assert result.out.rstrip("\n").endswith("<!-- cylist:end ATL -->")
    assert "Dry run" in result.err  # on stderr, so the block can be piped


# --- Where it writes --------------------------------------------------------


def test_it_writes_the_repository_root_not_the_directory_you_happen_to_be_in(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code reads the root's CLAUDE.md from every session inside the repo."""
    (tmp_path / ".git").mkdir()
    deep = tmp_path / "services" / "billing"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    result = run("repo", "setup", "ATL")

    assert result.code == 0
    assert _claude_md(tmp_path).is_file()
    assert not _claude_md(deep).exists()


def test_a_directory_given_with_path_means_the_claude_md_inside_it(
    run: Runner, tmp_path: Path
) -> None:
    result = run("repo", "setup", "ATL", "--path", str(tmp_path))

    assert result.code == 0
    assert _claude_md(tmp_path).is_file()


# --- Line endings, which is where Windows comes in --------------------------


def test_writing_translates_nothing_the_text_did_not_ask_for(tmp_path: Path) -> None:
    """The whole of the Windows story, in one assertion.

    Opened the ordinary way, Python turns every ``\\n`` this module writes into
    ``os.linesep`` — on Windows that is CRLF, so appending twenty lines to an
    LF file would silently rewrite every line in it. ``newline=""`` is what
    stops that, and this pins it on any platform.
    """
    path = tmp_path / "CLAUDE.md"

    repo._write(path, "a\nb\n", newline="\r\n")
    assert path.read_bytes() == b"a\r\nb\r\n"

    repo._write(path, "a\nb\n", newline="\n")
    assert path.read_bytes() == b"a\nb\n"


def test_a_crlf_file_stays_crlf(run: Runner, tmp_path: Path) -> None:
    path = _claude_md(tmp_path)
    path.write_bytes(b"# Atlas\r\n\r\nNotes.\r\n")

    result = run("repo", "setup", "ATL", "--path", str(path))

    assert result.code == 0
    raw = path.read_bytes()
    assert raw.startswith(b"# Atlas\r\n\r\nNotes.\r\n\r\n<!-- cylist:begin ATL -->\r\n")
    assert b"\n" not in raw.replace(b"\r\n", b""), "a bare LF was left in a CRLF file"


def test_an_lf_file_stays_lf(run: Runner, tmp_path: Path) -> None:
    path = _claude_md(tmp_path)
    path.write_bytes(b"# Atlas\n")

    result = run("repo", "setup", "ATL", "--path", str(path))

    assert result.code == 0
    assert b"\r" not in path.read_bytes()


def test_windows_is_handed_the_same_bytes_as_linux(
    run: Runner, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """There is no platform branch in this command, and this is what keeps it so.

    ``sys.platform`` is patched the way ``test_presence_portability`` does it.
    A new file is LF on both, because the command should produce the same
    bytes wherever it is run and LF is what git stores anyway.
    """
    linux, windows = tmp_path / "linux", tmp_path / "windows"
    linux.mkdir()
    windows.mkdir()
    run("repo", "setup", "ATL", "--path", str(linux))

    monkeypatch.setattr(sys, "platform", "win32")
    result = run("repo", "setup", "ATL", "--path", str(windows))

    assert result.code == 0
    assert _claude_md(windows).read_bytes() == _claude_md(linux).read_bytes()
    assert b"\r" not in _claude_md(windows).read_bytes()


# --- --json -----------------------------------------------------------------


def test_json_reports_the_file_and_what_happened_to_it(run: Runner, tmp_path: Path) -> None:
    result = run("--json", "repo", "setup", "ATL", "--path", str(tmp_path))

    payload = json.loads(result.out)
    assert payload["project"] == "ATL"
    assert payload["path"] == str(_claude_md(tmp_path))
    assert payload["result"] == "created"
    assert payload["written"] is True
    assert payload["block"].lstrip("\n").startswith("## Working with Cylist project ATL")


def test_json_says_a_dry_run_wrote_nothing(run: Runner, tmp_path: Path) -> None:
    result = run("--json", "repo", "setup", "ATL", "--path", str(tmp_path), "--dry-run")

    payload = json.loads(result.out)
    assert payload["result"] == "created"
    assert payload["written"] is False
    assert not _claude_md(tmp_path).exists()
