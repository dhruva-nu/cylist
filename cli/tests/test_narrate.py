"""One line on what the agent is doing, and nothing in it that should not be.

The line is read off a board by everybody who can see the board, so the
redaction cases matter more than the phrasing ones: a phrasing that reads
oddly is a cosmetic bug, a token that reaches the card is an incident.
"""

from __future__ import annotations

import pytest

from cylist_cli.presence.narrate import NOTE_MAX, describe_tool, redact, shorten_command


class TestTheLine:
    @pytest.mark.parametrize(
        ("tool", "tool_input", "line"),
        [
            (
                "Bash",
                {"command": "uv run pytest backend/tests/test_pr_states.py"},
                "Running pytest test_pr_states.py",
            ),
            ("Read", {"file_path": "/home/x/repo/backend/app/models/task.py"}, "Reading task.py"),
            ("Edit", {"file_path": "/home/x/repo/app/services/tasks.py"}, "Editing tasks.py"),
            ("MultiEdit", {"file_path": "C:\\repo\\app\\tasks.py"}, "Editing tasks.py"),
            ("Write", {"file_path": "/r/new_module.py"}, "Writing new_module.py"),
            ("Grep", {"pattern": "agent_session"}, "Searching for agent_session"),
            ("Glob", {"pattern": "**/*.tsx"}, "Finding **/*.tsx"),
            (
                "WebFetch",
                {"url": "https://docs.python.org/3/library/re.html"},
                "Fetching docs.python.org",
            ),
            ("Task", {"description": "Find the reaper"}, "Delegating: Find the reaper"),
            ("TodoWrite", {"todos": []}, "Updating its to-do list"),
            ("mcp__cylist__get_task", {"task": "CYLIST-84"}, "Calling cylist get_task CYLIST-84"),
            ("SomethingNew", {}, "Using SomethingNew"),
        ],
    )
    def test_each_tool_reads_as_a_sentence(
        self, tool: str, tool_input: dict[str, object], line: str
    ) -> None:
        assert describe_tool(tool, tool_input) == line

    def test_input_that_is_not_an_object_still_says_something(self) -> None:
        assert describe_tool("Read", None) == "Reading a file"

    def test_a_long_command_is_cut_to_a_line(self) -> None:
        line = describe_tool("Bash", {"command": "echo " + "word " * 200})

        assert len(line) <= NOTE_MAX
        assert line.endswith("…")

    def test_only_the_first_line_of_a_script_is_said(self) -> None:
        assert describe_tool("Bash", {"command": "make check\nmake test"}) == "Running make check"


class TestShorteningACommand:
    @pytest.mark.parametrize(
        ("command", "short"),
        [
            ("cd backend && uv run pytest -q", "pytest -q"),
            ("cd a && cd b && npm test", "npm test"),
            ("npm run lint", "lint"),
            ("uv --project backend run mypy app", "mypy app"),
            ("CYLIST_HOOK_DEBUG=1 cylist hook", "cylist hook"),
            ("npx vitest run src/api/realtime.test.ts", "vitest run realtime.test.ts"),
            ("git status", "git status"),
            ("curl https://example.com/a/b", "curl https://example.com/a/b"),
        ],
    )
    def test_the_part_that_says_what_it_does(self, command: str, short: str) -> None:
        assert shorten_command(command) == short


class TestRedaction:
    @pytest.mark.parametrize(
        ("text", "secret"),
        [
            ("curl -H 'Authorization: Bearer abcdefghijklmnop1234'", "abcdefghijklmnop1234"),
            ("PGPASSWORD=hunter2hunter2 psql", "hunter2hunter2"),
            ("export API_KEY=s3cr3t-value", "s3cr3t-value"),
            ('{"password": "correct horse"}', "correct horse"),
            ("--token abcdef123456", "abcdef123456"),
            ("postgresql://cylist:pa55word@db:5432/cylist", "pa55word"),
            ("cyl_" + "A" * 43, "cyl_" + "A" * 43),
            ("sk-ant-api03-" + "x" * 30, "sk-ant-api03-" + "x" * 30),
            ("ghp_" + "a1B2" * 9, "ghp_" + "a1B2" * 9),
            ("AKIAABCDEFGHIJKLMNOP", "AKIAABCDEFGHIJKLMNOP"),
            ("eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.SflKxwRJSMeKKF2QT4", "SflKxwRJSMeKKF2QT4"),
            ("key: Zx9Qw3Er5Ty7Ui9Op1As3Df5Gh7Jk9Lz", "Zx9Qw3Er5Ty7Ui9Op1As3Df5Gh7Jk9Lz"),
            (
                "-----BEGIN RSA PRIVATE KEY-----\nMIIabc\n-----END RSA PRIVATE KEY-----",
                "MIIabc",
            ),
        ],
    )
    def test_a_secret_never_survives(self, text: str, secret: str) -> None:
        cleaned = redact(text)

        assert secret not in cleaned
        assert "[redacted]" in cleaned

    @pytest.mark.parametrize(
        "text",
        [
            "git show 71ed9c9a1b2c3d4e5f60718293a4b5c6d7e8f901",
            "session 01a12c58-60d9-782a-aba7-aa01929a17fc",
            "pytest backend/tests/test_presence_portability.py",
            "14 passed in 2.31s",
        ],
    )
    def test_ordinary_output_is_left_alone(self, text: str) -> None:
        assert redact(text) == text
