"""``cylist hook`` — the Claude Code lifecycle hook, and its installer.

Claude Code runs ``cylist hook`` on every prompt, tool call, stop and exit,
with one JSON object on stdin saying which. This module turns those into
reports on the board, so a card's border can say *working*, *waiting* or
*done* without the agent having to say anything — the harness is the
witness, not the model.

Where those reports *go* changed in CYLIST-40. A hook lives for
milliseconds, and a connection that can be seen to end is worth more than a
sequence of requests that merely stops arriving, so the reports now go down
a unix socket to a small daemon that holds one WebSocket for the life of the
session — see :mod:`cylist_cli.presence`. The old
``PUT /tasks/{ref}/agent-sessions/{session_id}`` is still here and still
correct: it carries the first event of a session, before a daemon exists to
carry it, and it is what a machine that cannot run one falls back to.

Four rules shape everything here, and they are worth more than any feature:

* **An unbound session sends nothing.** The hooks are installed user-wide and
  fire in every project. Only ``/work <REF>`` typed in a session, or a
  ``cylist work <REF>`` launch, binds one to a card; a prompt that merely
  mentions a reference never does. Without a binding every event is a no-op
  and no HTTP is made, so unrelated sessions never appear on the board.
* **It always exits 0 and never writes to stdout** except the documented JSON.
  A hook that fails a prompt because the board was down would be a hook nobody
  kept installed. Bad JSON, no token, a network error — all swallowed, with a
  line on stderr only when ``CYLIST_HOOK_DEBUG=1``.
* **It is quick.** ``UserPromptSubmit`` is on the critical path of every
  prompt. The socket gets a quarter of a second and the HTTP fallback a
  second and a half, so the worst case is under two — well inside the three
  Claude Code allows. An ordinary tool call now costs nothing at all: it
  used to send a keepalive once a minute to prove the process was alive, and
  a held connection proves that by existing.
* **It never leaves anything behind it cannot show you.** The daemon it
  starts is one process per bound session, visible in ``cylist hook status``,
  stoppable with ``cylist hook stop``, gone when the session ends, and
  refusable entirely with ``CYLIST_PRESENCE=off``. A background process on
  somebody's laptop has to be all of those things.

State is one small file per session under ``$XDG_STATE_HOME/cylist/sessions``:
which card the session is bound to and whether it has been nudged. ``/clear``
ends one session and starts another in the same process; a ``handoff.json``
written on the way out and read on the way in is what carries the binding
across.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from cylist_cli import output, presence
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

HTTP_TIMEOUT = httpx.Timeout(1.5)
"""Total. A down backend costs a prompt this much and no more."""

HANDOFF_TTL = timedelta(seconds=5)
"""How old a ``/clear`` handoff may be and still be believed."""

FILE_MODE = 0o600

REFERENCE = re.compile(r"^[A-Z][A-Z0-9]*-\d+$")
"""A card's reference. Only this binds; a UUID in ``tool_input.task`` does not."""

WORK_COMMAND = re.compile(r"^\s*/work\s+(off|[A-Z][A-Z0-9]*-\d+)\s*$", re.IGNORECASE)
"""The one prompt that binds or unbinds. Anchored: "please don't touch
CYLIST-37" is not an instruction to the board. Case-insensitive because it is
typed by hand; the reference is upper-cased before it is used."""

WRITE_TOOLS = frozenset(
    {
        "move_task",
        "set_task_status",
        "add_comment",
        "create_subtask",
        "finish_subtask",
        "add_checklist_item",
        "set_checklist_item",
        "set_task_goal",
    }
)
"""The Cylist MCP tools that change a card. A bound session calling one of
these on a *different* card has moved to it; an unbound session calling one is
nudged, once, towards ``/work``."""

NOTIFICATION_REASONS = {
    "permission_prompt": "permission",
    "idle_prompt": "idle",
    "elicitation_dialog": "question",
}

TITLE_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})
"""The only two events whose output may rename the session."""

HOOK_EVENTS = (
    "SessionStart",
    "UserPromptSubmit",
    "PostToolUse",
    "Stop",
    "Notification",
    "SessionEnd",
)
NOTIFICATION_MATCHER = "|".join(NOTIFICATION_REASONS)
HOOK_TIMEOUT_SECONDS = 3

WORK_COMMAND_FILE = """\
---
description: Bind this session to a Cylist task and show it on the board
argument-hint: <TASK-REF | off>
---
This session is now bound to Cylist task $ARGUMENTS by the Cylist hook
(the board shows it as working / waiting / done automatically; you do not
need to report progress). If $ARGUMENTS is `off`, just acknowledge.
Otherwise fetch the task with `get_task`, restate it in two lines, and begin.
"""


def register(subparsers: Any) -> None:
    hook = subparsers.add_parser(
        "hook",
        help="The Claude Code lifecycle hook (reads one JSON event from stdin).",
        description=(
            "Run by Claude Code, not by you: reads one hook event from stdin and "
            "reports the session's state to the board when the session is bound "
            "to a card with /work. Always exits 0 and prints nothing but the "
            "JSON Claude Code expects. 'cylist hook install' wires it up."
        ),
    )
    # `swallow_errors` is read by main: a broken config file must not make the
    # hook fail a prompt any more than a down server may.
    hook.set_defaults(handler=_dispatch, swallow_errors=True)
    actions = hook.add_subparsers(dest="hook_action", required=False, metavar="<action>")

    install = actions.add_parser(
        "install",
        help="Add the hook to ~/.claude/settings.json and write the /work command.",
        description=(
            "Merges the hook into the user-level Claude Code settings (CLAUDE_CONFIG_DIR "
            "honoured), never duplicating it and never touching other hooks, and "
            "writes ~/.claude/commands/work.md. Safe to run again."
        ),
    )
    install.set_defaults(handler=_install, swallow_errors=False)

    uninstall = actions.add_parser(
        "uninstall", help="Remove the hook and the /work command; leave other hooks alone."
    )
    uninstall.set_defaults(handler=_uninstall, swallow_errors=False)

    # Not for people. Started by the hook, and only ever by the hook.
    daemon = actions.add_parser("daemon", help=argparse.SUPPRESS)
    daemon.add_argument("--session", required=True)
    daemon.set_defaults(handler=_daemon, swallow_errors=True)

    status = actions.add_parser(
        "status",
        help="Show the presence daemons running on this machine.",
        description=(
            "One line per bound session holding a connection to the board: which "
            "card, whether the link is up, and the process to stop if you want it "
            "gone. This exists so that a background process on your laptop is "
            "something you can see rather than something you find."
        ),
    )
    status.set_defaults(handler=_status, swallow_errors=False)

    stop = actions.add_parser(
        "stop",
        help="End a presence daemon, or all of them.",
        description=(
            "Ends the session on the board and exits the process. The next hook "
            "event starts a fresh one, so this is a way to clear something stuck "
            "rather than a way to opt out — CYLIST_PRESENCE=off is that."
        ),
    )
    stop.add_argument("--session", help="Just this one. Default: all of them.")
    stop.add_argument("--all", action="store_true", help="Every daemon on this machine.")
    stop.set_defaults(handler=_stop, swallow_errors=False)


# --- The daemon and its handles ----------------------------------------------


def _daemon(args: argparse.Namespace, ctx: Context) -> None:
    """Run the presence daemon for one session, in the foreground.

    Imported here and not at module scope: ``commands`` loads every module
    eagerly, and this one reaches ``websockets``. An unbound session — the
    common case, since the hooks fire in every project — must not pay for a
    dependency it will never use.
    """
    from cylist_cli.presence import daemon

    daemon.run(ctx, args.session)


def _status(_: argparse.Namespace, ctx: Context) -> None:
    rows = []
    for session in presence.supervisor.running_sessions():
        ack = presence.ipc.describe(session) or {}
        rows.append(
            [
                session[:12],
                str(ack.get("task") or "-"),
                str(ack.get("link") or "unreachable"),
                str(ack.get("pid") or "-"),
                str(presence.daemon_log(session)),
            ]
        )
    if ctx.as_json:
        output.emit_json(
            [dict(zip(["session", "task", "link", "pid", "log"], row, strict=True)) for row in rows]
        )
        return
    output.table(
        ["session", "card", "link", "pid", "log"],
        rows,
        empty="No presence daemons are running.",
    )


def _stop(args: argparse.Namespace, ctx: Context) -> None:
    wanted = [args.session] if args.session else presence.supervisor.running_sessions()
    stopped = [session for session in wanted if presence.supervisor.stop(session)]
    if ctx.as_json:
        output.emit_json({"stopped": stopped})
        return
    output.echo(f"Stopped {len(stopped)} presence daemon(s)." if stopped else "Nothing to stop.")


# --- The dispatcher ----------------------------------------------------------


def _dispatch(_: argparse.Namespace, ctx: Context) -> None:
    """One event in, at most one JSON object out, and never a failure."""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else None
        if not isinstance(event, dict):
            _debug("no JSON object on stdin")
            return
        reply = Hook(ctx, event).handle()
    except Exception as exc:  # the whole point: never fail the prompt
        _debug(f"{type(exc).__name__}: {exc}")
        return
    if reply:
        print(json.dumps(reply))


def _debug(line: str) -> None:
    if os.environ.get("CYLIST_HOOK_DEBUG") == "1":
        print(f"cylist hook: {line}", file=sys.stderr)


class Hook:
    """One hook event against one session's state file."""

    def __init__(self, ctx: Context, event: dict[str, Any]) -> None:
        self.ctx = ctx
        self.event = event
        self.name = str(event.get("hook_event_name") or "")
        self.session_id = str(event.get("session_id") or "")
        self.state = _load(self.session_id) if self.session_id else {}
        self.reply: dict[str, Any] = {}

    # -- Per event ------------------------------------------------------------

    def handle(self) -> dict[str, Any]:
        if not self.session_id:
            _debug("event without a session_id")
            return {}
        handler = getattr(self, f"_on_{self.name}", None)
        if handler is None:
            return {}
        handler()
        return self.reply

    def _on_SessionStart(self) -> None:  # noqa: N802 - named after the event
        wanted = os.environ.get("CYLIST_TASK", "").strip().upper()
        if REFERENCE.match(wanted):
            self._bind(wanted)
        elif not self.task and self.event.get("source") == "clear":
            carried = _take_handoff()
            if carried:
                self._bind(carried)
        if self.task:
            self._rename()
            self._put("working")

    def _on_UserPromptSubmit(self) -> None:  # noqa: N802
        match = WORK_COMMAND.match(str(self.event.get("prompt") or ""))
        if match:
            wanted = match.group(1)
            if wanted.lower() == "off":
                if self.task:
                    self._end("session_ended")
                _delete(self.session_id)
                return
            self._bind(wanted.upper())
            self._rename()
            self._put("working", bind=True)
            return
        if self.task:
            self._put("working")

    def _on_PostToolUse(self) -> None:  # noqa: N802
        target = self._cylist_write_target()
        if target and self.task and target != self.task:
            # The session has walked to another card; the server ends the old
            # row. No rename here — PostToolUse cannot set a title; the next
            # prompt will.
            self._bind(target)
            self._put("working", bind=True)
            return
        if target and not self.task:
            if not self.state.get("nudged"):
                self.reply["systemMessage"] = (
                    f"Tip: /work {target} shows this session on the board."
                )
                self.state["nudged"] = True
                _save(self.session_id, self.state)
            return
        # Nothing. An ordinary tool call used to cost a PUT once a minute,
        # to prove the process was still alive; the socket proves that by
        # existing. This is the event that got cheapest, and it is by far
        # the most frequent one.

    def _on_Stop(self) -> None:  # noqa: N802
        if self.task:
            self._put("waiting", reason="turn_ended")

    def _on_Notification(self) -> None:  # noqa: N802
        if not self.task:
            return
        kind = str(self.event.get("notification_type") or "")
        reason = NOTIFICATION_REASONS.get(kind)
        if reason is None:
            message = str(self.event.get("message") or "").lower()
            reason = "permission" if "permission" in message else "idle"
        self._put("waiting", reason=reason)

    def _on_SessionEnd(self) -> None:  # noqa: N802
        if not self.task:
            _delete(self.session_id)
            return
        self._end("session_ended")
        if self.event.get("reason") == "clear":
            _write_handoff(self.task)
        _delete(self.session_id)

    # -- Pieces -----------------------------------------------------------------

    @property
    def task(self) -> str | None:
        ref = self.state.get("task")
        return str(ref) if ref else None

    def _bind(self, ref: str) -> None:
        if self.task != ref:
            self.state["task"] = ref
            self.state["bound_at"] = _now().isoformat()
        _save(self.session_id, self.state)

    def _rename(self) -> None:
        """Ask Claude Code to call the session what the card is called."""
        if self.name not in TITLE_EVENTS or not self.task:
            return
        if self.event.get("session_title") == self.task:
            return
        self.reply["hookSpecificOutput"] = {
            "hookEventName": self.name,
            "sessionTitle": self.task,
        }
        self.state["title_applied"] = self.task
        _save(self.session_id, self.state)

    def _client_name(self) -> str:
        """What to call this session on the card.

        The reference, once the session has been renamed to it — which is the
        usual case, and reporting the name the session had *before* the rename
        would put "claude" on the card for one turn. Otherwise whatever the
        harness calls it.
        """
        if self.state.get("title_applied") == self.task:
            return str(self.task)
        return str(self.event.get("session_title") or self.task)

    def _cylist_write_target(self) -> str | None:
        """The card a Cylist write tool just changed, if this was one."""
        tool = str(self.event.get("tool_name") or "")
        if not tool.startswith("mcp__cylist"):
            return None
        parts = tool.split("__")
        if len(parts) < 3 or parts[-1] not in WRITE_TOOLS:
            return None
        arguments = self.event.get("tool_input")
        if not isinstance(arguments, dict):
            return None
        ref = str(arguments.get("task") or "").strip().upper()
        return ref if REFERENCE.match(ref) else None

    def _end(self, reason: str) -> None:
        """The session is over. Tell whichever channel is listening.

        The daemon is asked first and never started: a session that is
        ending has no use for a new one, and spawning here would leave a
        process behind to time itself out.
        """
        if presence.finish(cylist_argv(), self.session_id, reason):
            return
        # Straight to HTTP, deliberately not back through `_put`: that would
        # start a daemon, and a daemon for a session that is ending is a
        # process left behind to time itself out.
        self._http_put("done", reason=reason)

    def _put(self, state: str, *, reason: str | None = None, bind: bool = False) -> None:
        """Report to the board, or silently do not.

        The daemon first — it is holding a connection already, so this is a
        line on a unix socket and no HTTP at all. If there is no daemon, one
        is started for next time and *this* event goes the old way, because
        a new daemon has a server to reach before it is any use and a prompt
        must not wait for that.

        No token means this machine has never run ``cylist login``: the hooks
        may well be installed before that, and complaining about it on every
        prompt would be the wrong way to say so.
        """
        if not self.task:
            return
        if presence.report(
            cylist_argv(),
            self.session_id,
            self.task,
            state,
            reason,
            self._client_name(),
            bind=bind,
        ):
            return
        self._http_put(state, reason=reason)

    def _http_put(self, state: str, *, reason: str | None = None) -> None:
        """The original path, kept as the one that always works.

        Not a temporary crutch. It is what a machine with no unix sockets, a
        sandbox that forbids a fork, or a `CYLIST_PRESENCE=off` falls back
        to — and it is what carries the first event of every session, before
        a daemon exists to carry it.
        """
        if not self.task:
            return
        token = self.ctx.config.token
        if not token:
            _debug("no token configured; not reporting")
            return
        body: dict[str, Any] = {"state": state, "client_name": self._client_name()}
        if reason:
            body["reason"] = reason
        with self.ctx.build_client(token, timeout=HTTP_TIMEOUT) as client:
            client.put(f"/tasks/{self.task}/agent-sessions/{self.session_id}", body)


# --- State on disk -------------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def sessions_dir() -> Path:
    """``$XDG_STATE_HOME/cylist/sessions``, or its ``~/.local/state`` default."""
    root = os.environ.get("XDG_STATE_HOME")
    base = Path(root) if root else Path.home() / ".local" / "state"
    return base / "cylist" / "sessions"


def _state_path(session_id: str) -> Path:
    # A session id is a UUID from the harness, but it is still a path segment
    # from somebody else's process: keep it to one flat file name.
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
    return sessions_dir() / f"{safe}.json"


def _load(session_id: str) -> dict[str, Any]:
    try:
        loaded = json.loads(_state_path(session_id).read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _save(session_id: str, state: dict[str, Any]) -> None:
    _write_private(_state_path(session_id), json.dumps(state))


def _delete(session_id: str) -> None:
    _state_path(session_id).unlink(missing_ok=True)


def _handoff_path() -> Path:
    return sessions_dir() / "handoff.json"


def _write_handoff(task: str) -> None:
    _write_private(_handoff_path(), json.dumps({"task": task, "at": _now().isoformat()}))


def _take_handoff() -> str | None:
    """The card the session that just ``/clear``-ed was on, if that was seconds ago."""
    path = _handoff_path()
    try:
        carried = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return None
    finally:
        path.unlink(missing_ok=True)
    if not isinstance(carried, dict):
        return None
    try:
        fresh = _now() - datetime.fromisoformat(str(carried.get("at"))) <= HANDOFF_TTL
    except ValueError:
        return None
    task = str(carried.get("task") or "")
    return task if fresh and REFERENCE.match(task) else None


def _write_private(path: Path, content: str) -> None:
    """Create 0600 rather than create-then-chmod, as ``config.save`` does."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, FILE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(content)
    os.chmod(path, FILE_MODE)  # noqa: PTH101 - an existing file keeps its old mode


# --- Installing ------------------------------------------------------------------


def claude_config_dir() -> Path:
    root = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(root) if root else Path.home() / ".claude"


def cylist_argv() -> list[str]:
    """How to invoke this CLI, as an argv, without the subcommand.

    Absolute, because a hook runs with whatever PATH the harness happened to
    inherit, and ``uv tool install`` and a venv put ``cylist`` in different
    places. Falls back to the module form for a checkout run with ``uv run``.

    An argv rather than a string because two callers want it and they want
    different things: the installer writes a shell command into settings.json,
    and the supervisor execs a list.
    """
    found = shutil.which("cylist")
    if found:
        return [str(Path(found).resolve())]
    invoked = Path(sys.argv[0])
    if invoked.name == "cylist" and invoked.is_absolute():
        return [str(invoked.resolve())]
    return [str(Path(sys.executable).resolve()), "-m", "cylist_cli"]


def hook_command() -> str:
    """The absolute command Claude Code should run, as settings.json wants it."""
    return " ".join([*cylist_argv(), "hook"])


def _is_ours(command: Any) -> bool:
    """Any install of this hook, wherever the binary was at the time."""
    text = str(command or "")
    return text.endswith("cylist hook") or text.endswith("-m cylist_cli hook")


def _entry(command: str) -> dict[str, Any]:
    return {"type": "command", "command": command, "timeout": HOOK_TIMEOUT_SECONDS}


def _install(_: argparse.Namespace, ctx: Context) -> None:
    command = hook_command()
    settings_path = claude_config_dir() / "settings.json"
    settings = _read_settings(settings_path)
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        raise _not_a_settings_file(settings_path, "'hooks' is not an object")

    added: list[str] = []
    for event in HOOK_EVENTS:
        groups = hooks.setdefault(event, [])
        if not isinstance(groups, list):
            raise _not_a_settings_file(settings_path, f"hooks.{event} is not a list")
        present = any(
            _is_ours(entry.get("command"))
            for group in groups
            if isinstance(group, dict)
            for entry in group.get("hooks", [])
            if isinstance(entry, dict)
        )
        if present:
            # Ours already, possibly from an older install elsewhere on disk:
            # point it at where the binary is now rather than adding a second.
            for group in groups:
                for entry in group.get("hooks", []) if isinstance(group, dict) else []:
                    if isinstance(entry, dict) and _is_ours(entry.get("command")):
                        entry["command"] = command
            continue
        fresh: dict[str, Any] = {"hooks": [_entry(command)]}
        if event == "Notification":
            fresh = {"matcher": NOTIFICATION_MATCHER, **fresh}
        groups.append(fresh)
        added.append(event)

    _write_settings(settings_path, settings)

    command_path = claude_config_dir() / "commands" / "work.md"
    command_path.parent.mkdir(parents=True, exist_ok=True)
    wrote_command = (
        not command_path.exists() or command_path.read_text("utf-8") != WORK_COMMAND_FILE
    )
    command_path.write_text(WORK_COMMAND_FILE, "utf-8")

    if ctx.as_json:
        output.emit_json(
            {
                "settings": str(settings_path),
                "command": command,
                "added": added,
                "work_command": str(command_path),
            }
        )
        return
    if added:
        output.echo(f"Added the Cylist hook to {settings_path} for: {', '.join(added)}.")
    else:
        output.echo(f"The Cylist hook was already in {settings_path}; nothing to add.")
    output.echo(f"Hook command: {command}")
    output.echo(
        f"{'Wrote' if wrote_command else 'Kept'} {command_path} — type /work <REF> in a session."
    )
    output.echo("Open a new Claude Code session for the hooks to load.")


def _uninstall(_: argparse.Namespace, ctx: Context) -> None:
    settings_path = claude_config_dir() / "settings.json"
    settings = _read_settings(settings_path)
    hooks = settings.get("hooks")
    removed: list[str] = []
    if isinstance(hooks, dict):
        for event in list(hooks):
            groups = hooks.get(event)
            if not isinstance(groups, list):
                continue
            kept: list[Any] = []
            for group in groups:
                if not isinstance(group, dict):
                    kept.append(group)
                    continue
                entries = [
                    entry
                    for entry in group.get("hooks", [])
                    if not (isinstance(entry, dict) and _is_ours(entry.get("command")))
                ]
                if len(entries) != len(group.get("hooks", [])):
                    removed.append(event)
                if entries:
                    kept.append({**group, "hooks": entries})
            if kept:
                hooks[event] = kept
            else:
                del hooks[event]
        _write_settings(settings_path, settings)

    command_path = claude_config_dir() / "commands" / "work.md"
    had_command = command_path.exists()
    command_path.unlink(missing_ok=True)

    # Uninstalling has to leave nothing running. Nothing would start a daemon
    # again once the hooks are gone, so any still holding a session would sit
    # there until their own idle window — which is a background process left
    # behind by an uninstall, and there is no good version of that.
    stopped = [
        session
        for session in presence.supervisor.running_sessions()
        if presence.supervisor.stop(session)
    ]

    if ctx.as_json:
        output.emit_json(
            {
                "settings": str(settings_path),
                "removed": sorted(set(removed)),
                "daemons_stopped": stopped,
            }
        )
        return
    if stopped:
        output.echo(f"Stopped {len(stopped)} presence daemon(s).")
    if removed:
        output.echo(f"Removed the Cylist hook from {settings_path}.")
    else:
        output.echo(f"No Cylist hook in {settings_path}; nothing to remove.")
    if had_command:
        output.echo(f"Removed {command_path}.")


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
