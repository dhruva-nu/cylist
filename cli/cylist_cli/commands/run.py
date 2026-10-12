"""``cylist run ATL-41`` — Cylist runs the agent itself, and the board is its UI.

The other way onto a card is ``cylist work``, which hands the terminal to
``claude`` and watches it through hooks. This one runs the agent through the
Claude Agent SDK instead (see :mod:`cylist_cli.runner`): what it is doing
shows on the card, its questions and tool permissions are answered there, and
the terminal only echoes along. With no prompt given, the card is the prompt.

Needs the optional SDK: ``uv tool install 'cylist-cli[agent]'``. Imported
lazily, so every other command, and the hook above all, pays nothing for it.
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import sys
from typing import Any

from cylist_cli import output
from cylist_cli.commands.hook import REFERENCE
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

PERMISSION_MODES = ("default", "acceptEdits", "plan", "bypassPermissions")


def register(subparsers: Any) -> None:
    run = subparsers.add_parser(
        "run",
        help="Run an agent on a task, answered from the board.",
        description=(
            "Starts Claude through the Agent SDK on the task. The card shows what "
            "it is doing; its questions and tool permissions are answered on the "
            "board. With no PROMPT, the card's title and description are the prompt. "
            "After each turn you may reply here, or press Enter to finish."
        ),
    )
    run.add_argument("task", metavar="TASK", help="Task reference, e.g. ATL-41.")
    run.add_argument("prompt", metavar="PROMPT", nargs="?", help="What to do. Default: the card.")
    run.add_argument("--model", help="Model to run, e.g. opus. Default: Claude Code's.")
    run.add_argument(
        "--permission-mode",
        choices=PERMISSION_MODES,
        help="Claude Code permission mode. Default: your settings'.",
    )
    run.set_defaults(handler=_run)


def _run(args: argparse.Namespace, ctx: Context) -> None:
    ref = str(args.task).strip().upper()
    if not REFERENCE.match(ref):
        raise CylistError(f"{args.task!r} is not a task reference, like ATL-41.")
    try:
        runner = importlib.import_module("cylist_cli.runner")
    except ImportError as exc:
        raise CylistError(
            "cylist run needs the Claude Agent SDK. Install the CLI with its agent "
            "extra: uv tool install 'cylist-cli[agent]'"
        ) from exc

    task = ctx.client.get(f"/tasks/{ref}")
    output.warn(f"{task.get('reference', ref)}  {task.get('title', '')}")
    prompt = args.prompt or runner.briefing(task)
    asyncio.run(
        runner.run_agent(
            ctx,
            task,
            prompt,
            model=args.model,
            permission_mode=args.permission_mode,
            interactive=sys.stdin.isatty(),
        )
    )
