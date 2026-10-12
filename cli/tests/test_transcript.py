"""Reading a Claude Code transcript as a card's feed — CYLIST-88.

The records below are the shapes the harness writes, trimmed to the fields
that matter. What is asserted is as much what is left out — a person's
prompt, the model's thinking, a file's contents — as what is put in.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cylist_cli.presence.transcript import Narrator, Tail

CWD = "/home/someone/cylist"
AT = "2026-10-12T14:31:02.123Z"


def assistant(*blocks: dict[str, Any], **more: Any) -> dict[str, Any]:
    return {
        "type": "assistant",
        "timestamp": AT,
        "cwd": CWD,
        "message": {"content": list(blocks)},
        **more,
    }


def user(content: Any, **more: Any) -> dict[str, Any]:
    return {"type": "user", "timestamp": AT, "cwd": CWD, "message": {"content": content}, **more}


def tool_use(name: str, tool_input: dict[str, Any], id: str = "toolu_1") -> dict[str, Any]:
    return {"type": "tool_use", "id": id, "name": name, "input": tool_input}


def result(content: Any, id: str = "toolu_1", is_error: bool = False) -> dict[str, Any]:
    return {"type": "tool_result", "tool_use_id": id, "content": content, "is_error": is_error}


def lines(narrator: Narrator, *records: dict[str, Any]) -> list[tuple[str, str, str | None]]:
    return [
        (entry.kind, entry.text, entry.tool)
        for record in records
        for entry in narrator.read_line(json.dumps(record))
    ]


class TestWhatGoesIn:
    def test_the_agent_s_words(self) -> None:
        assert lines(
            Narrator(), assistant({"type": "text", "text": "Starting with the model."})
        ) == [("said", "Starting with the model.", None)]

    def test_each_kind_of_tool_call_and_what_it_acted_on(self) -> None:
        found = lines(
            Narrator(),
            assistant(
                tool_use("Read", {"file_path": f"{CWD}/backend/app/models/task.py"}, "a"),
                tool_use("Edit", {"file_path": f"{CWD}/frontend/src/PrLink.tsx"}, "b"),
                tool_use("Bash", {"command": "cd backend && uv run pytest tests/test_x.py"}, "c"),
                tool_use("mcp__cylist__get_task", {"task": "CYLIST-88"}, "d"),
            ),
        )

        assert found == [
            ("read", "backend/app/models/task.py", "Read"),
            ("edit", "frontend/src/PrLink.tsx", "Edit"),
            ("run", "pytest test_x.py", "Bash"),
            ("tool", "Calling cylist get_task CYLIST-88", "mcp__cylist__get_task"),
        ]

    def test_a_command_s_verdict_is_its_last_line(self) -> None:
        narrator = Narrator()

        found = lines(
            narrator,
            assistant(tool_use("Bash", {"command": "pytest"})),
            user([result("collected 14 items\n\n..............\n14 passed in 2.31s\n")]),
        )

        assert found[-1] == ("output", "14 passed in 2.31s", "Bash")

    def test_a_failure_says_why_whatever_the_tool(self) -> None:
        narrator = Narrator()

        found = lines(
            narrator,
            assistant(tool_use("Edit", {"file_path": f"{CWD}/a.py"})),
            user(
                [result([{"type": "text", "text": "String to replace not found."}], is_error=True)]
            ),
        )

        assert found[-1] == ("error", "String to replace not found.", "Edit")

    def test_the_timestamp_is_the_transcript_s(self) -> None:
        (entry,) = Narrator().read_line(json.dumps(assistant({"type": "text", "text": "hi"})))
        assert entry.at.startswith("2026-10-12T14:31:02.123")


class TestWhatStaysOut:
    def test_a_file_s_contents(self) -> None:
        narrator = Narrator()

        found = lines(
            narrator,
            assistant(tool_use("Read", {"file_path": f"{CWD}/secrets.env"})),
            user([result("STRIPE_KEY=sk_live_abc\nDEBUG=1\n")]),
        )

        assert found == [("read", "secrets.env", "Read")]

    def test_a_person_s_prompt_and_the_model_s_thinking(self) -> None:
        found = lines(
            Narrator(),
            user("please fix the board"),
            assistant({"type": "thinking", "thinking": "hmm"}),
        )

        assert found == []

    def test_a_subagent_s_conversation_and_the_harness_s_notes(self) -> None:
        found = lines(
            Narrator(),
            assistant({"type": "text", "text": "from a subagent"}, isSidechain=True),
            user([{"type": "text", "text": "<command-name>"}], isMeta=True),
        )

        assert found == []

    def test_records_that_are_not_messages(self) -> None:
        narrator = Narrator()
        assert narrator.read_line('{"type": "file-history-snapshot"}') == []
        assert narrator.read_line("not json") == []
        assert narrator.read_line("[1, 2]") == []

    def test_secrets_in_what_it_says_and_what_comes_back(self) -> None:
        narrator = Narrator()

        found = lines(
            narrator,
            assistant(
                {"type": "text", "text": "Using token cyl_abcdefghijklmnopqrstu to log in."},
                tool_use("Bash", {"command": "PGPASSWORD=hunter2hunter2 psql -c 'select 1'"}),
            ),
            user([result("Authorization: Bearer abcdefghijklmnop1234")]),
        )

        flat = " ".join(text for _, text, _ in found)
        assert "cyl_abcdefghijklmnopqrstu" not in flat
        assert "hunter2hunter2" not in flat
        assert "abcdefghijklmnop1234" not in flat


class TestFollowingTheFile:
    def test_it_starts_at_the_end(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text('{"before": true}\n')

        tail = Tail(str(path))
        with path.open("a") as handle:
            handle.write('{"after": true}\n')

        assert tail.read() == ['{"after": true}']

    def test_a_half_written_line_waits_for_its_end(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text("")
        tail = Tail(str(path))

        with path.open("a") as handle:
            handle.write('{"half": ')
        assert tail.read() == []

        with path.open("a") as handle:
            handle.write("true}\n")
        assert tail.read() == ['{"half": true}']

    def test_windows_line_endings_are_not_part_of_the_line(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_bytes(b"")
        tail = Tail(str(path))

        with path.open("ab") as handle:
            handle.write(b'{"a": 1}\r\n{"b": 2}\r\n')
        assert tail.read() == ['{"a": 1}', '{"b": 2}']

    def test_a_file_that_is_not_there_yet_is_waited_for(self, tmp_path: Path) -> None:
        path = tmp_path / "later.jsonl"
        tail = Tail(str(path))
        assert tail.read() == []

        path.write_text('{"first": true}\n')
        assert tail.read() == ['{"first": true}']

    def test_a_rewritten_file_is_followed_from_its_new_end(self, tmp_path: Path) -> None:
        path = tmp_path / "t.jsonl"
        path.write_text('{"a": 1}\n{"b": 2}\n')
        tail = Tail(str(path))

        path.write_text('{"c": 3}\n')
        assert tail.read() == []
        with path.open("a") as handle:
            handle.write('{"d": 4}\n')
        assert tail.read() == ['{"d": 4}']
