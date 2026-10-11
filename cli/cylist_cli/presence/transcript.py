"""Following a Claude Code transcript, and reading it as a feed — CYLIST-88.

Every hook event names the session's transcript: a JSONL file the harness
appends a record to for everything that happens in the conversation. The
daemon follows it the way ``tail -f`` would, and what it finds becomes the
card's live feed — what the agent read, edited, ran and said.

Two pieces, and only one of them touches a file:

* :class:`Narrator` turns one transcript record into feed lines. No IO; it
  remembers only which tool each pending call was, so that a result can be
  told apart from the call it answers. Where the rules are, and where the
  tests are.
* :class:`Tail` reads whole new lines from a file that is still being
  written, and nothing else.

What leaves the machine is the agent's text, a one-line subject for each tool
call, and — for shell commands and failures only — one line of what came
back. Never a file's contents, never a whole command's output, and never
anything that has not been through :func:`.narrate.redact`.
"""

from __future__ import annotations

import json
import os
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from cylist_cli.presence import narrate

PENDING_TOOLS = 256
"""How many unanswered tool calls to remember. Calls are answered in seconds;
this only bounds a transcript that never answers some."""

READ_CHUNK = 256 * 1024
"""Most bytes read from the transcript per poll. A turn that pasted a large
file catches up over a few polls instead of in one allocation."""


@dataclass(frozen=True)
class FeedEntry:
    """One line of the feed, as the server takes it."""

    at: str
    kind: str
    text: str
    tool: str | None = None

    def wire(self) -> dict[str, Any]:
        return {"at": self.at, "kind": self.kind, "tool": self.tool, "text": self.text}


class Narrator:
    """Transcript records in, feed lines out."""

    def __init__(self) -> None:
        self._tools: OrderedDict[str, str] = OrderedDict()

    def read_line(self, raw: str) -> list[FeedEntry]:
        """The feed lines one JSONL line of a transcript amounts to — often none."""
        try:
            record = json.loads(raw)
        except ValueError:
            return []
        return self.read(record) if isinstance(record, dict) else []

    def read(self, record: dict[str, Any]) -> list[FeedEntry]:
        # A subagent's own conversation, and the harness's notes to itself,
        # are not what this agent said or did.
        if record.get("isSidechain") or record.get("isMeta"):
            return []
        message = record.get("message")
        if not isinstance(message, dict):
            return []
        content = message.get("content")
        if not isinstance(content, list):
            # A plain string is a person's prompt — theirs, not the agent's.
            return []
        at = _when(record.get("timestamp"))
        cwd = str(record.get("cwd") or "")
        kind = record.get("type")
        entries: list[FeedEntry] = []
        for block in content:
            if not isinstance(block, dict):
                continue
            if kind == "assistant":
                entries.extend(self._from_assistant(block, at, cwd))
            elif kind == "user":
                entries.extend(self._from_result(block, at))
        return entries

    def _from_assistant(self, block: dict[str, Any], at: str, cwd: str) -> list[FeedEntry]:
        if block.get("type") == "text":
            text = narrate.clip(narrate.redact(str(block.get("text") or "")), narrate.SAID_MAX)
            return [FeedEntry(at, "said", text)] if text else []
        if block.get("type") == "tool_use":
            tool = str(block.get("name") or "")
            self._remember(str(block.get("id") or ""), tool)
            subject = narrate.feed_subject(tool, block.get("input"), cwd)
            return [FeedEntry(at, narrate.kind_of(tool), subject, tool or None)]
        # Thinking stays the model's own.
        return []

    def _from_result(self, block: dict[str, Any], at: str) -> list[FeedEntry]:
        if block.get("type") != "tool_result":
            return []
        tool = self._tools.pop(str(block.get("tool_use_id") or ""), "")
        text = _result_text(block.get("content"))
        if block.get("is_error"):
            line = _first_line(text) or "Failed."
            return [FeedEntry(at, "error", _clean(line), tool or None)]
        if tool == "Bash":
            # The last thing a command printed is usually the verdict:
            # "14 passed in 2.31s", "Found 0 errors.", "nothing to commit".
            line = _last_line(text)
            return [FeedEntry(at, "output", _clean(line), tool)] if line else []
        return []

    def _remember(self, tool_use_id: str, tool: str) -> None:
        if not tool_use_id:
            return
        self._tools[tool_use_id] = tool
        while len(self._tools) > PENDING_TOOLS:
            self._tools.popitem(last=False)


class Tail:
    """New whole lines of a file that another process is appending to.

    Starts at the end: a session bound halfway through has a transcript full
    of work that happened before anyone asked the board to watch, and the
    feed is about what happens from here. A partial last line is held back
    until its newline arrives, so a record is never parsed half-written. A
    file that shrinks — rewritten, not appended to — is followed from its new
    end rather than misread from an offset past it.
    """

    def __init__(self, path: str) -> None:
        self.path = path
        self._offset = _size(path)
        self._partial = b""

    def read(self) -> list[str]:
        size = _size(self.path)
        if size < self._offset:
            self._offset, self._partial = size, b""
        if size == self._offset:
            return []
        try:
            with open(self.path, "rb") as handle:  # noqa: PTH123 - a seek, not a read_text
                handle.seek(self._offset)
                chunk = handle.read(READ_CHUNK)
        except OSError:
            return []
        self._offset += len(chunk)
        data = self._partial + chunk
        *whole, self._partial = data.split(b"\n")
        return [line.decode("utf-8", "replace") for line in whole if line.strip()]


def _size(path: str) -> int:
    try:
        return os.stat(path).st_size  # noqa: PTH116 - one call, no Path to build
    except OSError:
        return 0


def _when(value: Any) -> str:
    """The record's own timestamp, or now if it has none the server could read."""
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).isoformat()
        except ValueError:
            pass
    return datetime.now(UTC).isoformat()


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            str(part.get("text") or "")
            for part in content
            if isinstance(part, dict) and part.get("type") == "text"
        )
    return ""


def _first_line(text: str) -> str:
    return next((line for line in text.splitlines() if line.strip()), "")


def _last_line(text: str) -> str:
    return next((line for line in reversed(text.splitlines()) if line.strip()), "")


def _clean(line: str) -> str:
    return narrate.clip(narrate.redact(line), narrate.FEED_LINE_MAX)
