"""Wiring ``cylist hook`` into Claude Code, and taking it out again.

Two things are installed, both under the user's Claude Code configuration
directory (``CLAUDE_CONFIG_DIR``, or ``~/.claude``): one entry per lifecycle
event in ``settings.json``, and the slash commands in ``commands/``. This
module edits those files and nothing else. Saying what it did is left to the
callers — ``cylist hook install`` prints all of it and ``cylist setup`` one
line among several — and so is building the command to install, because that
depends on where the running CLI lives.

``settings.json`` is somebody's own file. Everything here edits it in place,
recognises its own entries however an earlier install wrote them, and leaves
every other entry — and every value that is not the shape Claude Code writes —
exactly where it was.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cylist_cli.errors import CylistError

HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "Stop",
    "Notification",
    "SessionEnd",
)

NOTIFICATION_REASONS = {
    "permission_prompt": "permission",
    "idle_prompt": "idle",
    "elicitation_dialog": "question",
}
"""The notifications the hook reports, and the waiting reason each becomes."""

NOTIFICATION_MATCHER = "|".join(NOTIFICATION_REASONS)
HOOK_TIMEOUT_SECONDS = 3

PERMISSION_TIMEOUT_SECONDS = 600
"""The one hook allowed to be slow: ``PermissionRequest`` waits for a person.

Claude Code's own default for a command hook, set explicitly so a reinstall
over an older entry raises it from three. The daemon gives up at 580 and the
server stops offering the prompt at 590, so the harness never has to kill the
hook to be rid of it."""

HOOK_TIMEOUTS = {"PermissionRequest": PERMISSION_TIMEOUT_SECONDS}
"""Per event, where it differs from :data:`HOOK_TIMEOUT_SECONDS`."""

WORK_COMMAND_FILE = """\
---
description: Bind this session to a Cylist task and show it on the board
argument-hint: <TASK-REF | off>
---
This session is now bound to Cylist task $ARGUMENTS by the Cylist hook
(the board shows it as working / waiting / done automatically; you do not
need to report progress). If $ARGUMENTS is `off`, just acknowledge.
Otherwise fetch the task with `get_task`, restate it in two lines, and
begin. As you work, whenever you would open the code to answer a question,
ask the project's docs first with `ask_docs`; an `ok` answer is the section
to act on. Any other status means the docs do not know yet: find it in the
code, then straight away `place_doc` what you found and make the edits it
plans with `write_doc`, so the next agent to ask is answered — short,
factual sentences, not a progress report.

Writing the code is not finishing the card. Finish it by raising a pull
request whose body shows the work running: a screenshot of every change
that has a screen, and the checks you ran with their result. Then
`add_comment` the pull request's link on the card and `move_task` it to the
column the board keeps for work that is raised and waiting to be deployed —
`get_project` names the columns, and on most boards it is `Dev`. If the
work cannot be raised, leave the card where it is and say why.
"""

SETUP_COMMAND_FILE = """\
---
description: Teach this repository's CLAUDE.md how to work with a Cylist project
argument-hint: <PROJECT-KEY>
---
Set the repository you are in up to work against Cylist project $ARGUMENTS.

Run `cylist repo setup $ARGUMENTS` from the root of this repository. It checks
the project exists before it writes anything, reads that project's columns,
docs and skills off the board, and puts a block into CLAUDE.md fenced by
`<!-- cylist:begin $ARGUMENTS -->` markers — appending it, or replacing the
block it wrote last time, so nothing else in the file is touched and running
it twice changes nothing. Add `--dry-run` to see the block without writing it,
and `--path` to write a CLAUDE.md somewhere other than the repository root.

Then say in two lines where it was written and what it now tells a session
about $ARGUMENTS. Write none of the block by hand: it is read live off the
board, and a version composed from memory is wrong. If `cylist` is not found,
say that this machine has not had `cylist setup` run on it (or that the
install is not on PATH) — do not improvise the block instead.
"""

SLASH_COMMANDS: tuple[tuple[str, str, str], ...] = (
    ("work.md", WORK_COMMAND_FILE, "type /work <REF> in a session"),
    ("cylist-setup.md", SETUP_COMMAND_FILE, "type /cylist-setup <KEY> in any repository"),
)
"""Every ``~/.claude/commands/*.md`` this installs: file name, body, what to say.

A table rather than a line each inside the installer, so that shipping another
slash command is a constant and an entry here, and ``uninstall`` cannot forget
one that ``install`` writes.
"""

OUR_HOOK_COMMAND = re.compile(
    r'^"?(?:.*[\\/])?(?:cylist(?:\.exe)?"?\s|.*-m\s+cylist_cli\s)hook$', re.IGNORECASE
)
r"""Any install of this hook, quoted or not, with or without the ``.exe``.

Matched rather than suffix-tested because quoting moved the end of the
string: ``"C:\...\cylist.exe" hook`` no longer ends in ``cylist hook``, and
an installer that could not recognise its own earlier work would add a
second copy of the hook on every run.

Case-insensitive for the same reason, and it is not hypothetical:
``shutil.which`` on Windows appends the extension *verbatim* from
``%PATHEXT%``, which is conventionally upper case, so
:func:`cylist_cli.system.cylist_argv` routinely returns ``…\cylist.EXE``.
Matching only ``.exe`` would mean ``cylist setup`` — which promises to be safe
to run again — adding a second hook every time it was run on Windows.
"""


def claude_config_dir() -> Path:
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(root) if root else Path.home() / ".claude"


def settings_path() -> Path:
    return claude_config_dir() / "settings.json"


def commands_dir() -> Path:
    return claude_config_dir() / "commands"


def command_line(argv: list[str]) -> str:
    r"""An argv as the command string settings.json holds.

    Quoted where it has to be. A POSIX install lands in a path without
    spaces often enough to have got away with plain joining; ``C:\Users\Given
    Name\...\cylist.exe`` does not, and an unquoted one would have the shell
    run ``C:\Users\Given`` on every prompt.

    Double quotes, which are what both ``cmd.exe`` and a POSIX shell take.
    Not ``shlex.quote``: it picks single quotes, which ``cmd.exe`` does not
    treat as quoting at all. A Windows path cannot contain ``"`` — the
    filesystem forbids it — so there is nothing here left to escape.
    """
    return " ".join(f'"{part}"' if " " in part else part for part in argv)


def is_our_hook(command: Any) -> bool:
    return bool(OUR_HOOK_COMMAND.match(str(command or "").strip()))


# --- Installing ------------------------------------------------------------------


@dataclass(frozen=True)
class CommandFile:
    """One slash command the install wrote, and whether it had to."""

    path: Path
    wrote: bool
    hint: str
    """What to tell the user they can now type."""


@dataclass(frozen=True)
class HookInstall:
    """What installing the hooks changed, for whoever wants to report it.

    ``cylist setup`` prints one line of this among several; ``cylist hook
    install`` prints all of it. Separating the doing from the saying is what
    lets both exist without one of them shelling out to the other.
    """

    settings_path: Path
    command: str
    added: list[str]
    commands: list[CommandFile]

    def as_json(self) -> dict[str, Any]:
        """The shape both commands print under ``--json``."""
        return {
            "settings": str(self.settings_path),
            "command": self.command,
            "added": self.added,
            "commands": [str(command.path) for command in self.commands],
        }


def install(command: str) -> HookInstall:
    """Wire ``command`` into Claude Code's user settings, idempotently.

    Never duplicates the hook and never touches anybody else's: an install
    that finds ours already there only re-points it at wherever the binary is
    now, which is what makes this safe to run again after a reinstall.
    """
    path = settings_path()
    settings = _read_settings(path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise _not_a_settings_file(path, "'hooks' is not an object")

    added: list[str] = []
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise _not_a_settings_file(path, f"hooks.{event} is not a list")
        ours = _our_entries(groups)
        if ours:
            # Ours already, possibly from an older install elsewhere on disk:
            # point it at where the binary is now rather than adding a second.
            for entry in ours:
                entry["command"] = command
                entry["timeout"] = _timeout_for(event)
            continue
        fresh: dict[str, Any] = {"hooks": [_hook_entry(command, event)]}
        if event == "Notification":
            fresh = {"matcher": NOTIFICATION_MATCHER, **fresh}
        groups.append(fresh)
        added.append(event)

    _write_settings(path, settings)

    return HookInstall(
        settings_path=path,
        command=command,
        added=added,
        commands=_write_slash_commands(),
    )


def _hook_entry(command: str, event: str) -> dict[str, Any]:
    return {"type": "command", "command": command, "timeout": _timeout_for(event)}


def _timeout_for(event: str) -> int:
    return HOOK_TIMEOUTS.get(event, HOOK_TIMEOUT_SECONDS)


def _group_entries(group: dict[str, Any]) -> list[Any]:
    """A hook group's entries, or none when ``hooks`` is not the list it should be."""
    entries = group.get("hooks", [])
    return entries if isinstance(entries, list) else []


def _our_entries(groups: list[Any]) -> list[dict[str, Any]]:
    """The entries in one event's hook groups that run this hook.

    Skips anything that is not the shape Claude Code writes rather than
    failing on it: this is somebody's own settings file, and a stray value in
    it is theirs to keep.
    """
    found: list[dict[str, Any]] = []
    for group in groups:
        if not isinstance(group, dict):
            continue
        for entry in _group_entries(group):
            if isinstance(entry, dict) and is_our_hook(entry.get("command")):
                found.append(entry)
    return found


def _write_slash_commands() -> list[CommandFile]:
    """Put every ``SLASH_COMMANDS`` entry in ``commands/``, rewriting only what differs.

    A file whose bytes already match is left alone so that an install says
    "kept" rather than "wrote" — and so that an editor watching the directory
    is not woken by a run that changed nothing. One edited by hand *is*
    overwritten: the contract these carry has to be the one this version of
    the CLI ships, not whatever somebody tried last month.
    """
    directory = commands_dir()
    directory.mkdir(parents=True, exist_ok=True)
    written: list[CommandFile] = []
    for name, body, hint in SLASH_COMMANDS:
        path = directory / name
        differs = not path.exists() or path.read_text("utf-8") != body
        if differs:
            path.write_text(body, "utf-8")
        written.append(CommandFile(path=path, wrote=differs, hint=hint))
    return written


# --- Uninstalling ----------------------------------------------------------------


@dataclass(frozen=True)
class HookUninstall:
    """What uninstalling took out of Claude Code's configuration."""

    settings_path: Path
    removed: list[str]
    """The events that had this hook, one entry per group it was taken from."""
    commands_removed: list[Path]


def uninstall() -> HookUninstall:
    """Take this hook and its slash commands out, leaving everything else."""
    path = settings_path()
    settings = _read_settings(path)
    hooks = settings.get("hooks")
    removed: list[str] = []
    if isinstance(hooks, dict):
        removed = _remove_our_hooks(hooks)
        _write_settings(path, settings)

    directory = commands_dir()
    commands_removed = [
        directory / name for name, _, _ in SLASH_COMMANDS if (directory / name).exists()
    ]
    for command in commands_removed:
        command.unlink(missing_ok=True)

    return HookUninstall(settings_path=path, removed=removed, commands_removed=commands_removed)


def _remove_our_hooks(hooks: dict[str, Any]) -> list[str]:
    """Take this hook out of every event, in place, and say which events had it.

    Everybody else's entries stay exactly where they were. A group left with
    no entries goes, and so does an event left with no groups, so that an
    uninstall leaves the file as it would have been without us.
    """
    removed: list[str] = []
    for event in list(hooks):
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept: list[Any] = []
        for group in groups:
            entries = group.get("hooks") if isinstance(group, dict) else None
            if not isinstance(entries, list):
                kept.append(group)
                continue
            theirs = [
                entry
                for entry in entries
                if not (isinstance(entry, dict) and is_our_hook(entry.get("command")))
            ]
            if len(theirs) != len(entries):
                removed.append(event)
            if theirs:
                kept.append({**group, "hooks": theirs})
        if kept:
            hooks[event] = kept
        else:
            del hooks[event]
    return removed


# --- The settings file -----------------------------------------------------------


def _read_settings(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text("utf-8"))
    except FileNotFoundError:
        return {}
    except ValueError as exc:
        raise _not_a_settings_file(path, str(exc)) from exc
    if not isinstance(loaded, dict):
        raise _not_a_settings_file(path, "the top level is not an object")
    return loaded


def _write_settings(path: Path, settings: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", "utf-8")


def _not_a_settings_file(path: Path, why: str) -> CylistError:
    return CylistError(f"{path} is not a Claude Code settings file I can edit: {why}.")
