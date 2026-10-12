"""``cylist hook`` — the Claude Code lifecycle hook, and the commands around it.

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
* **It is quick** — with one exception. ``PermissionRequest`` waits for a
  person, up to ten minutes, and is installed with a timeout to match; it is
  the only event that does, and it only waits when the board can answer.
  Everything else stays fast. ``UserPromptSubmit`` is on the critical path of every
  prompt. The socket gets a quarter of a second and the HTTP fallback a
  second and a half — all of the addresses the server answers on, between
  them, not each — so the worst case is under two, well inside the three
  Claude Code allows. A tool call costs one line on that socket: its
  ``PreToolUse`` says what the agent is about to do — "Running pytest
  test_pr_states.py" — which the card shows as the session's note. It used
  to send a keepalive once a minute instead, to prove the process was alive;
  a held connection proves that by existing.
* **It never leaves anything behind it cannot show you.** The daemon it
  starts is one process per bound session, visible in ``cylist hook status``,
  stoppable with ``cylist hook stop``, gone when the session ends, and
  refusable entirely with ``CYLIST_PRESENCE=off``. A background process on
  somebody's laptop has to be all of those things.

What the hook remembers between events is in :mod:`.hook_state`; how it is
written into Claude Code's settings, and taken out again, is in
:mod:`.hook_install`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import uuid
from typing import Any

import httpx

from cylist_cli import output, presence, system
from cylist_cli.commands import hook_install, hook_state
from cylist_cli.commands.hook_install import HOOK_EVENTS as HOOK_EVENTS
from cylist_cli.commands.hook_install import NOTIFICATION_REASONS, HookInstall
from cylist_cli.commands.hook_install import SLASH_COMMANDS as SLASH_COMMANDS
from cylist_cli.commands.hook_install import claude_config_dir as claude_config_dir
from cylist_cli.commands.hook_state import sessions_dir as sessions_dir
from cylist_cli.context import Context
from cylist_cli.presence import narrate
from cylist_cli.system import cylist_argv as cylist_argv

HTTP_TIMEOUT = httpx.Timeout(1.5)
"""Total. A down backend costs a prompt this much and no more."""

HTTP_BUDGET = 1.5
"""And the same, in total, however many addresses the server answers on.

A configuration may hold three (see :mod:`cylist_cli.config`) and a prompt
cannot pay three timeouts to discover that none of them is there. Addresses
that refuse instantly — the usual shape of a wrong one, a ``localhost`` with
nothing behind it — cost nothing and leave the budget to the next; one that
hangs spends it, and is the last thing tried.
"""

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

TITLE_EVENTS = frozenset({"SessionStart", "UserPromptSubmit"})
"""The only two events whose output may rename the session."""

PERMISSION_ASK_WAIT = 585.0
"""How long the hook waits on the daemon's reply: the daemon's own 580, and a
little for the reply to travel."""

BRIEFED_STARTS = frozenset({"startup", "clear", "compact"})
"""The ``SessionStart`` sources that begin with no memory of the card, and so
are told what it is and to read its docs. ``resume`` is not one: the
transcript it resumes already holds the brief it was given the first time."""


def working_brief(ref: str) -> str:
    """What a session bound to a card is told at the start, before any prompt.

    The same ask as the ``/work`` command, for the sessions that were bound
    without one — ``cylist work ATL-41``, or a ``/clear`` that carried the
    binding across — and would otherwise start on the card without asking
    what the agents before them wrote down about its project.
    """
    key = ref.split("-", 1)[0]
    return (
        f"This session is bound to Cylist task {ref}; the board shows its progress "
        f"automatically. Before you start on it, call get_task for {ref}. As you "
        "work, whenever you would open the code to answer a question, first "
        f"ask_docs for {key}; an 'ok' answer is the section to act on. Any other "
        "status means the docs do not know yet: find it in the code, then straight "
        "away place_doc what you found and make the edits it plans with write_doc, "
        "so the next agent to ask is answered — short, factual sentences, not a "
        "progress report. Writing the code is not finishing the card: raise a pull "
        "request whose body shows the work running — a screenshot of every change "
        "that has a screen, and the checks you ran with their result — then "
        f"add_comment its link on {ref} and move_task it to the column the board "
        "keeps for work that is raised and waiting to be deployed (get_project "
        "names them; on most boards it is Dev). If the work cannot be raised, "
        "leave the card where it is and say why."
    )


def hook_command() -> str:
    """The absolute command Claude Code should run, as settings.json wants it."""
    return hook_install.command_line([*cylist_argv(), "hook"])


def install_hooks() -> HookInstall:
    """Wire ``cylist hook`` into Claude Code's user settings, idempotently.

    See :func:`.hook_install.install`. The command is built here, from where
    this CLI is running now, so that a reinstall re-points the hook at it.
    """
    return hook_install.install(hook_command())


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
        help="Add the hook to ~/.claude/settings.json and write the slash commands.",
        description=(
            "Merges the hook into the user-level Claude Code settings (CLAUDE_CONFIG_DIR "
            "honoured), never duplicating it and never touching other hooks, and "
            "writes ~/.claude/commands/work.md and cylist-setup.md. Safe to run again."
        ),
    )
    install.set_defaults(handler=_install, swallow_errors=False)

    uninstall = actions.add_parser(
        "uninstall", help="Remove the hook and its slash commands; leave other hooks alone."
    )
    uninstall.set_defaults(handler=_uninstall, swallow_errors=False)

    # Not for people. Started by the hook, and only ever by the hook.
    daemon = actions.add_parser("daemon", help=argparse.SUPPRESS)
    daemon.add_argument("--session", required=True)
    # The `claude` this session belongs to, found by the hook that spawned us.
    # Absent on a platform that would not say, and the daemon then keeps the
    # five-minute idle window instead. `--owner-started` is opaque: whatever
    # the platform calls the moment that pid began, compared only with another
    # reading of the same pid.
    daemon.add_argument("--owner-pid", type=int)
    daemon.add_argument("--owner-started", default="")
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


# --- Installing and uninstalling ---------------------------------------------


def _install(_: argparse.Namespace, ctx: Context) -> None:
    report = install_hooks()

    if ctx.as_json:
        output.emit_json(report.as_json())
        return
    if report.added:
        output.echo(
            f"Added the Cylist hook to {report.settings_path} for: {', '.join(report.added)}."
        )
    else:
        output.echo(f"The Cylist hook was already in {report.settings_path}; nothing to add.")
    output.echo(f"Hook command: {report.command}")
    for command in report.commands:
        output.echo(f"{'Wrote' if command.wrote else 'Kept'} {command.path} — {command.hint}.")
    output.echo("Open a new Claude Code session for the hooks to load.")


def _uninstall(_: argparse.Namespace, ctx: Context) -> None:
    report = hook_install.uninstall()

    # Uninstalling has to leave nothing running. Nothing would start a daemon
    # again once the hooks are gone, so any still holding a session would sit
    # there until their own idle window — which is a background process left
    # behind by an uninstall, and there is no good version of that.
    stopped = _stop_sessions(presence.supervisor.running_sessions())

    if ctx.as_json:
        output.emit_json(
            {
                "settings": str(report.settings_path),
                "removed": sorted(set(report.removed)),
                "commands_removed": [str(path) for path in report.commands_removed],
                "daemons_stopped": stopped,
            }
        )
        return
    if stopped:
        output.echo(f"Stopped {len(stopped)} presence daemon(s).")
    if report.removed:
        output.echo(f"Removed the Cylist hook from {report.settings_path}.")
    else:
        output.echo(f"No Cylist hook in {report.settings_path}; nothing to remove.")
    for path in report.commands_removed:
        output.echo(f"Removed {path}.")


# --- The daemon and its handles ----------------------------------------------


def _daemon(args: argparse.Namespace, ctx: Context) -> None:
    """Run the presence daemon for one session, in the foreground.

    Imported here and not at module scope: ``commands`` loads every module
    eagerly, and this one reaches ``websockets``. An unbound session — the
    common case, since the hooks fire in every project — must not pay for a
    dependency it will never use.
    """
    from cylist_cli.presence import daemon, owner

    found = (
        owner.Owner(pid=args.owner_pid, started=args.owner_started or "")
        if args.owner_pid
        else None
    )
    daemon.run(ctx, args.session, owner=found)


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
    stopped = _stop_sessions(wanted)
    if ctx.as_json:
        output.emit_json({"stopped": stopped})
        return
    output.echo(f"Stopped {len(stopped)} presence daemon(s)." if stopped else "Nothing to stop.")


def _stop_sessions(sessions: list[str]) -> list[str]:
    """Stop each of these sessions' daemons, and say which ones there were to stop."""
    return [session for session in sessions if presence.supervisor.stop(session)]


# --- The dispatcher ----------------------------------------------------------


def _dispatch(_: argparse.Namespace, ctx: Context) -> None:
    """One event in, at most one JSON object out, and never a failure."""
    try:
        raw = sys.stdin.read()
        event = json.loads(raw) if raw.strip() else None
        if not isinstance(event, dict):
            _debug("no JSON object on stdin")
            return
        reply = Hook(ctx, event).respond()
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
        self.state = hook_state.load_state(self.session_id) if self.session_id else {}
        self.reply: dict[str, Any] = {}

    # -- Per event ------------------------------------------------------------

    def respond(self) -> dict[str, Any]:
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
            carried = hook_state.take_handoff()
            if carried and REFERENCE.match(carried):
                self._bind(carried)
        if self.task:
            self._rename()
            if self.event.get("source") in BRIEFED_STARTS:
                self._brief()
            self._report_state("working")

    def _on_UserPromptSubmit(self) -> None:  # noqa: N802
        match = WORK_COMMAND.match(str(self.event.get("prompt") or ""))
        if match:
            wanted = match.group(1)
            if wanted.lower() == "off":
                if self.task:
                    self._report_end("session_ended")
                hook_state.delete_state(self.session_id)
                return
            self._bind(wanted.upper())
            self._rename()
            self._report_state("working", bind=True)
            return
        if self.task:
            self._report_state("working")

    def _on_PreToolUse(self) -> None:  # noqa: N802
        """Say what the agent is about to do, as the session's note.

        *Pre*, not post: "Running pytest" is true while pytest runs, and a
        note sent after it finished would always be one step behind. Not an
        entry in the activity trail — the server keeps one note per session
        and replaces it, so a hundred tool calls are a hundred updates to one
        line rather than a hundred lines.
        """
        if self.task:
            note = narrate.describe_tool(self._tool_name(), self.event.get("tool_input"))
            self._report_state("working", note=note)

    def _on_PermissionRequest(self) -> None:  # noqa: N802
        """Ask the board, as well as the terminal, whether a tool may run — CYLIST-89.

        Claude Code calls this when it is about to show its permission dialog.
        The daemon puts the request on the card; a person picks Allow once,
        Allow for this session or Deny there, and this hook returns that as
        the harness's decision. The terminal's dialog is open meanwhile and
        the first answer wins. Claude Code does not end this hook when the
        terminal is answered, so the tool's ``PostToolUse`` says so instead
        (see :meth:`_on_PostToolUse`), the daemon takes the prompt down and
        hands this hook "withdrawn", and it exits saying nothing. A denial in
        the terminal runs no tool; the session's next report takes it down.

        Silent — and so the terminal's to decide — whenever the board cannot
        answer: an unbound session, no daemon running, presence off, nobody
        answering before the daemon gives up.
        """
        if not self.task:
            return
        tool = self._tool_name()
        tool_input = self.event.get("tool_input")
        prompt_id = str(uuid.uuid4())
        # Marked on the session so this tool's PostToolUse — which comes if
        # the terminal answers first — knows there is a prompt to take down.
        self.state["asking"] = prompt_id
        hook_state.save_state(self.session_id, self.state)
        try:
            answer = presence.ask_permission(
                self.session_id,
                self.task,
                self._client_name(),
                prompt_id,
                narrate.describe_tool(tool, tool_input),
                tool or None,
                transcript=self._transcript(),
                wait=PERMISSION_ASK_WAIT,
            )
        finally:
            hook_state.forget_asking(self.session_id, prompt_id)
        decision = permission_decision(
            answer, tool, tool_input, self.event.get("permission_suggestions")
        )
        if decision is not None:
            self._hook_specific_output()["decision"] = decision

    def _on_PostToolUse(self) -> None:  # noqa: N802
        if self.task and self.state.get("asking"):
            # A permission is on the card and this tool ran anyway: the
            # terminal answered it first.
            presence.tool_ran(self.session_id)
        target = self._cylist_write_target()
        if target is None:
            # Nothing. An ordinary tool call used to cost a PUT once a minute,
            # to prove the process was still alive; the socket proves that by
            # existing. This is the event that got cheapest, and it is by far
            # the most frequent one.
            return
        if not self.task:
            self._nudge_towards(target)
        elif target != self.task:
            # The session has walked to another card; the server ends the old
            # row. No rename here — PostToolUse cannot set a title; the next
            # prompt will.
            self._bind(target)
            self._report_state("working", bind=True)

    def _on_Stop(self) -> None:  # noqa: N802
        if self.task:
            self._report_state("waiting", reason="turn_ended")

    def _on_Notification(self) -> None:  # noqa: N802
        if not self.task:
            return
        message = str(self.event.get("message") or "")
        reason = NOTIFICATION_REASONS.get(str(self.event.get("notification_type") or ""))
        if reason is None:
            reason = "permission" if "permission" in message.lower() else "idle"
        # The harness's own words — "Claude needs your permission to use
        # Bash" — are the best line the card has for what it is waiting on.
        said = narrate.clip(narrate.redact(message), narrate.NOTE_MAX)
        self._report_state("waiting", reason=reason, note=said or None)

    def _on_SessionEnd(self) -> None:  # noqa: N802
        if self.task:
            self._report_end("session_ended")
            if self.event.get("reason") == "clear":
                hook_state.write_handoff(self.task)
        hook_state.delete_state(self.session_id)

    # -- Pieces -----------------------------------------------------------------

    @property
    def task(self) -> str | None:
        ref = self.state.get("task")
        return str(ref) if ref else None

    def _bind(self, ref: str) -> None:
        if self.task != ref:
            self.state["task"] = ref
            self.state["bound_at"] = hook_state.now().isoformat()
        hook_state.save_state(self.session_id, self.state)

    def _nudge_towards(self, target: str) -> None:
        """Suggest ``/work`` to an unbound session writing to a card — once."""
        if self.state.get("nudged"):
            return
        self.reply["systemMessage"] = f"Tip: /work {target} shows this session on the board."
        self.state["nudged"] = True
        hook_state.save_state(self.session_id, self.state)

    def _rename(self) -> None:
        """Ask Claude Code to call the session what the card is called."""
        if self.name not in TITLE_EVENTS or not self.task:
            return
        if self.event.get("session_title") == self.task:
            return
        self._hook_specific_output()["sessionTitle"] = self.task
        self.state["title_applied"] = self.task
        hook_state.save_state(self.session_id, self.state)

    def _brief(self) -> None:
        """Put the card, and its docs, in front of the model."""
        if self.task:
            self._hook_specific_output()["additionalContext"] = working_brief(self.task)

    def _hook_specific_output(self) -> dict[str, Any]:
        """The event-specific half of the reply, which a title and a brief share."""
        specific: dict[str, Any] = self.reply.setdefault(
            "hookSpecificOutput", {"hookEventName": self.name}
        )
        return specific

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

    def _tool_name(self) -> str:
        return str(self.event.get("tool_name") or "")

    def _cylist_write_target(self) -> str | None:
        """The card a Cylist write tool just changed, if this was one."""
        tool = self._tool_name()
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

    def _transcript(self) -> str | None:
        """The file the daemon follows for the card's feed, as the harness named it."""
        path = self.event.get("transcript_path")
        return path if isinstance(path, str) and path else None

    def _report_end(self, reason: str) -> None:
        """The session is over. Tell whichever channel is listening.

        The daemon is asked first and never started: a session that is
        ending has no use for a new one, and spawning here would leave a
        process behind to time itself out. For the same reason a daemon that
        is not there sends this straight to HTTP rather than back through
        :meth:`_report_state`, which would start one.
        """
        if presence.finish(self.session_id, reason):
            return
        self._http_put("done", reason=reason)

    def _report_state(
        self,
        state: str,
        *,
        reason: str | None = None,
        bind: bool = False,
        note: str | None = None,
    ) -> None:
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
            cylist_argv,
            self.session_id,
            self.task,
            state,
            reason,
            self._client_name(),
            bind=bind,
            note=note,
            transcript=self._transcript(),
        ):
            return
        self._http_put(state, reason=reason, note=note)

    def _http_put(self, state: str, *, reason: str | None = None, note: str | None = None) -> None:
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
        if note:
            body["note"] = note
        with self.ctx.build_client(token, timeout=HTTP_TIMEOUT, budget=HTTP_BUDGET) as client:
            client.put(f"/tasks/{self.task}/agent-sessions/{self.session_id}", body)


# --- The board's answer to a permission ----------------------------------------


def permission_decision(
    answer: dict[str, Any] | None,
    tool: str,
    tool_input: Any,
    suggestions: Any,
) -> dict[str, Any] | None:
    """Claude Code's ``PermissionRequest`` decision for an answer from the board.

    ``None`` when there is no answer to give — the prompt closed on the board
    without one — which leaves the terminal's dialog to decide.

    "Allow for this session" adds allow *rules* for this session only, never
    to a settings file. Where the harness suggested rules to add, those are
    used with their destination narrowed to ``session``; otherwise the rule
    is the tool — and for a shell command the exact command, never the whole
    of Bash. Other suggestions are never taken: the harness offers ones such
    as switching the session to accept every edit, which is far more than
    "this, for this session" and not what a person clicking it meant.
    """
    if not answer or answer.get("status") != "answered":
        return None
    key = answer.get("key")
    if key == "allow_once":
        return {"behavior": "allow"}
    if key == "allow_session":
        return {
            "behavior": "allow",
            "updatedPermissions": _session_permissions(tool, tool_input, suggestions),
        }
    if key == "deny":
        return {"behavior": "deny", "message": "Denied from the Cylist board."}
    return None


def _session_permissions(tool: str, tool_input: Any, suggestions: Any) -> list[dict[str, Any]]:
    suggested = [
        {**entry, "destination": "session"}
        for entry in (suggestions if isinstance(suggestions, list) else [])
        if isinstance(entry, dict)
        and entry.get("type") == "addRules"
        and entry.get("behavior") == "allow"
        and isinstance(entry.get("rules"), list)
    ]
    if suggested:
        return suggested
    rule: dict[str, Any] = {"toolName": tool}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if tool == "Bash" and isinstance(command, str) and command.strip():
        rule["ruleContent"] = command.strip()
    return [{"type": "addRules", "rules": [rule], "behavior": "allow", "destination": "session"}]


# --- Names from before the split -----------------------------------------------
# Session state and the installer were part of this module once, and the suite
# still reaches them through it. Aliases rather than copies, so there is one
# definition of each.

_now = hook_state.now
_state_path = hook_state.state_path
_load_state = hook_state.load_state
_save_state = hook_state.save_state
_handoff_path = hook_state.handoff_path
_write_handoff = hook_state.write_handoff
_write_private = system.write_private
_is_our_hook = hook_install.is_our_hook
