"""``cylist work ATL-41`` — open Claude Code on a card, bound to it from the start.

The other way to bind is to type ``/work ATL-41`` into a session already
running. This one is for the case where you are going to the terminal *for*
the card: it checks the card exists, names the session after it, and hands
the terminal to ``claude`` with ``CYLIST_TASK`` set, which the ``SessionStart``
hook reads to bind before the first prompt.

``-n <REF>`` is passed as well as the environment variable. The hook renames
the session itself — but through a field Claude Code has not documented yet,
so the documented flag rides along as the fallback that makes the name right
even if that field stops being honoured.
"""

from __future__ import annotations

import argparse
import os
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from cylist_cli import output
from cylist_cli.commands.hook import REFERENCE
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

Exec = Callable[[str, Sequence[str], Mapping[str, str]], Any]


def _replace_process(file: str, argv: Sequence[str], env: Mapping[str, str]) -> None:
    os.execvpe(file, list(argv), dict(env))  # noqa: S606 - handing the terminal to claude is the command


_exec: Exec = _replace_process
"""Replaced by the tests, which cannot hand the process to ``claude``."""


def register(subparsers: Any) -> None:
    work = subparsers.add_parser(
        "work",
        help="Open Claude Code on a task, shown on the board as you work.",
        description=(
            "Checks the task exists, then replaces this process with "
            "'claude -n <REF>' bound to it, so the card's border on the board "
            "follows the session: working, waiting on you, done. Anything after "
            "'--' is passed to claude. Needs 'cylist hook install' once first."
        ),
    )
    work.add_argument("task", metavar="TASK", help="Task reference, e.g. ATL-41.")
    work.add_argument(
        "claude_args",
        nargs=argparse.REMAINDER,
        metavar="-- CLAUDE ARGS",
        help="Passed to claude as given.",
    )
    work.set_defaults(handler=_work)


def _work(args: argparse.Namespace, ctx: Context) -> None:
    ref = str(args.task).strip().upper()
    if not REFERENCE.match(ref):
        raise CylistError(
            f"{args.task!r} is not a task reference. The hook binds on a reference like "
            "ATL-41, not an id."
        )

    task = ctx.client.get(f"/tasks/{ref}")
    ref = str(task.get("reference") or ref)

    extra = [arg for arg in args.claude_args if arg != "--"]
    if not ctx.as_json:
        output.warn(f"{ref}  {task.get('title', '')}")
        output.warn("Opening Claude Code bound to it…")

    environment = {**os.environ, "CYLIST_TASK": ref}
    _exec("claude", ["claude", "-n", ref, *extra], environment)
