"""Saying what an agent is doing, in one line a person can read off a card.

Pure: a tool name and its input go in, a sentence comes out. The hook calls
:func:`describe_tool` on every ``PreToolUse`` and sends the answer as the
session's note, which is what turns "Agent working" on a card into "Running
pytest test_pr_states.py".

Everything that leaves the machine this way passes :func:`redact` first. A
note is read by everyone who can see the board, and a command line is where
people paste tokens: ``curl -H 'Authorization: Bearer …'``,
``PGPASSWORD=… psql``. The board is the wrong place to learn that a secret was
in one, so the redaction happens here, before the line is ever sent, rather
than on the server after it has already travelled.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any

NOTE_MAX = 120
"""Longest note this sends. The server takes more; a card line shows less."""

REDACTED = "[redacted]"

_SENSITIVE_NAME = (
    r"(?:[A-Za-z0-9_.-]*(?:pass(?:word|wd)?|secret|token|api[_-]?key|access[_-]?key|"
    r"private[_-]?key|credential|auth)[A-Za-z0-9_.-]*)"
)

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # A PEM block, whole. Before anything else, so nothing below chews it up first.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
            re.DOTALL,
        ),
        REDACTED,
    ),
    # Authorization: Bearer … / Basic …, as a header or a bare scheme.
    (re.compile(r"(?i)\b(bearer|basic|token)\s+[A-Za-z0-9._~+/=-]{8,}"), rf"\1 {REDACTED}"),
    # A password in a URL: scheme://user:secret@host.
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s:/@]+):[^\s@/]+@"), rf"\1:{REDACTED}@"),
    # NAME=value, NAME: value, "name": "value", --name value, where the name says secret.
    (
        re.compile(
            rf"(?i)(\"?\b{_SENSITIVE_NAME}\"?\s*[:=]\s*|--{_SENSITIVE_NAME}[= ]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;&|]+)"
        ),
        rf"\1{REDACTED}",
    ),
    # Shapes that are secrets whatever they are called.
    (re.compile(r"\bcyl_[A-Za-z0-9_-]{8,}"), REDACTED),  # a Cylist token
    (re.compile(r"\bsk-[A-Za-z0-9_-]{16,}"), REDACTED),  # Anthropic, OpenAI
    (re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"), REDACTED),
    (re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), REDACTED),  # AWS access key id
    (re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}"), REDACTED),  # Slack
    (re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b"), REDACTED),  # Google API key
    (re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"), REDACTED),  # JWT
)

_LONG_OPAQUE = re.compile(r"(?<![A-Za-z0-9_+/=-])[A-Za-z0-9_+/=-]{32,}(?![A-Za-z0-9_+/=-])")


def redact(text: str) -> str:
    """``text`` with anything that looks like a credential replaced.

    Errs towards hiding. A redacted commit hash costs a reader nothing; a
    token on a board costs a rotation. Long opaque strings are hidden when
    they mix case and digits the way generated keys do — but not when they are
    plain hex, which is what hashes and ids look like.
    """
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return _LONG_OPAQUE.sub(_hide_if_keylike, text)


def _hide_if_keylike(match: re.Match[str]) -> str:
    word = match.group(0)
    if re.fullmatch(r"[0-9a-fA-F-]+", word):
        return word  # a hash or a uuid
    has_upper = any(c.isupper() for c in word)
    has_lower = any(c.islower() for c in word)
    has_digit = any(c.isdigit() for c in word)
    return REDACTED if has_upper and has_lower and has_digit else word


def clip(text: str, limit: int) -> str:
    """One line of at most ``limit`` characters, with an ellipsis if cut."""
    line = " ".join(text.split())
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


def describe_tool(tool_name: str, tool_input: Any) -> str:
    """One line on what a tool call is about to do, already redacted.

    The tools a coding agent spends its day in get a sentence of their own;
    anything else is "Using <tool>", which is still more than "working".
    """
    arguments: dict[str, Any] = tool_input if isinstance(tool_input, dict) else {}
    line = _describe(tool_name, arguments)
    return clip(redact(line), NOTE_MAX)


def _describe(tool: str, arguments: dict[str, Any]) -> str:
    path = _text(arguments.get("file_path") or arguments.get("notebook_path"))
    if tool == "Bash":
        return f"Running {shorten_command(_text(arguments.get('command')))}".rstrip()
    if tool == "Read":
        return f"Reading {file_name(path)}"
    if tool in {"Edit", "MultiEdit", "NotebookEdit"}:
        return f"Editing {file_name(path)}"
    if tool == "Write":
        return f"Writing {file_name(path)}"
    if tool == "Grep":
        return f"Searching for {_text(arguments.get('pattern'))}"
    if tool == "Glob":
        return f"Finding {_text(arguments.get('pattern'))}"
    if tool == "WebFetch":
        return f"Fetching {_host(_text(arguments.get('url')))}"
    if tool == "WebSearch":
        return f"Searching the web for {_text(arguments.get('query'))}"
    if tool in {"Task", "Agent"}:
        return f"Delegating: {_text(arguments.get('description')) or 'a subtask'}"
    if tool == "TodoWrite":
        return "Updating its to-do list"
    if tool == "Skill":
        return f"Using the {_text(arguments.get('skill')) or 'a'} skill"
    if tool.startswith("mcp__"):
        return _describe_mcp(tool, arguments)
    return f"Using {tool}" if tool else "Working"


def _describe_mcp(tool: str, arguments: dict[str, Any]) -> str:
    """``mcp__cylist__get_task`` as "cylist get_task CYLIST-84"."""
    parts = tool.split("__")
    server = parts[1] if len(parts) > 2 else ""
    name = parts[-1]
    subject = _text(arguments.get("task") or arguments.get("project"))
    words = ["Calling", server.replace("claude_ai_", ""), name, subject]
    return " ".join(word for word in words if word)


_RUNNERS = frozenset({"npx", "bunx", "env"})
"""Followed straight by the command they run."""

_WRAPPERS = frozenset({"uv", "npm", "poetry", "pipenv", "pnpm", "yarn"})
"""Followed by the command they run only after ``run``/``exec`` — ``npm test``
is a command of its own, ``npm run test`` is ``test``."""

_WRAPPER_VERBS = frozenset({"run", "exec", "x"})


def shorten_command(command: str) -> str:
    """The part of a command line that says what it does.

    ``cd backend && uv run pytest backend/tests/test_pr_states.py -q`` is
    "pytest test_pr_states.py -q": the first line only, a leading ``cd …&&``
    and ``FOO=bar`` assignments dropped, a runner like ``uv run`` peeled off,
    and paths cut to the file they name.
    """
    first = command.strip().splitlines()[0] if command.strip() else ""
    while hop := re.match(r"^\s*cd\s+\S+\s*&&\s*", first):
        first = first[hop.end() :]
    words = first.split()
    while words and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", words[0]):
        words.pop(0)
    if words and words[0] in _RUNNERS:
        words.pop(0)
    elif words and words[0] in _WRAPPERS:
        # `uv --project backend run pytest`: the verb may sit behind a flag or two.
        verb = next((i for i, word in enumerate(words[1:5], 1) if word in _WRAPPER_VERBS), None)
        if verb is not None and verb + 1 < len(words):
            words = words[verb + 1 :]
    return " ".join(file_name(word) if _looks_like_path(word) else word for word in words)


def _looks_like_path(word: str) -> bool:
    return ("/" in word or "\\" in word) and "://" not in word and not word.startswith("-")


def file_name(path: str) -> str:
    """The last part of a path, on either kind of separator."""
    if not path:
        return "a file"
    name = PureWindowsPath(path).name if "\\" in path else PurePosixPath(path).name
    return name or path


def _host(url: str) -> str:
    found = re.match(r"^[a-z]+://([^/\s?#]+)", url)
    return found.group(1) if found else url


def _text(value: Any) -> str:
    return value if isinstance(value, str) else ""
