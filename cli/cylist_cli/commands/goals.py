"""``cylist goals …`` — the epics a board's cards are written under."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output
from cylist_cli.context import Context

STATUSES = ("open", "achieved", "dropped")
NAME_WIDTH = 34


def register(subparsers: Any) -> None:
    goals = subparsers.add_parser(
        "goals",
        help="A project's goals.",
        description=(
            "A goal is an outcome a board's cards are work towards — an epic. "
            "It has a colour of its own, which every card on it wears on the "
            "board, and a progress count taken from those cards rather than "
            "kept beside them."
        ),
    )
    actions = goals.add_subparsers(dest="action", required=True, metavar="<action>")

    ls = actions.add_parser("ls", help="List a project's goals.")
    ls.add_argument("project", metavar="PROJECT", help="Project key or id, e.g. ATL.")
    ls.add_argument(
        "--open", action="store_true", help="Leave out goals that are achieved or dropped."
    )
    ls.set_defaults(handler=_list)

    show = actions.add_parser("show", help="One goal and the cards on it.")
    show.add_argument("goal", metavar="GOAL", help="Goal reference or id, e.g. ATL-G1.")
    show.set_defaults(handler=_show)

    new = actions.add_parser("new", help="Start a goal.")
    new.add_argument("project", metavar="PROJECT", help="Project key or id.")
    new.add_argument("--name", required=True)
    new.add_argument("--description", default="", help="What reaching it means.")
    new.add_argument("--owner", required=True, help="A project member's id.")
    new.add_argument("--colour", help="Six-digit hex. Omit to take one from the palette.")
    new.add_argument("--target", metavar="DATE", help="ISO date it is wanted by, e.g. 2026-12-01.")
    new.set_defaults(handler=_new)

    status = actions.add_parser(
        "status",
        help="Mark a goal achieved, dropped, or open again.",
        description=(
            "Marking a goal achieved is refused while a card on it is neither "
            "in the board's last column nor cancelled; the refusal names every "
            "card holding it open. Dropping one is never refused."
        ),
    )
    status.add_argument("goal", metavar="GOAL", help="Goal reference or id.")
    status.add_argument("state", metavar="STATE", choices=STATUSES)
    status.set_defaults(handler=_status)

    link = actions.add_parser("link", help="Put a card on a goal.")
    link.add_argument("task", metavar="TASK", help="Task reference or id, e.g. ATL-41.")
    link.add_argument("goal", metavar="GOAL", help="Goal reference or id, e.g. ATL-G1.")
    link.set_defaults(handler=_link)

    unlink = actions.add_parser("unlink", help="Take a card off the goal it is on.")
    unlink.add_argument("task", metavar="TASK", help="Task reference or id.")
    unlink.set_defaults(handler=_unlink)


def _list(args: argparse.Namespace, ctx: Context) -> None:
    goals = ctx.client.get(f"/projects/{args.project}/goals", open_only=True if args.open else None)

    if ctx.as_json:
        output.emit_json(goals)
        return

    rows = [
        [
            str(goal["reference"]),
            output.truncate(str(goal["name"]), NAME_WIDTH),
            str(goal["status"]),
            _progress(goal),
            str(goal.get("target_date") or ""),
            str((goal.get("owner") or {}).get("name", "")),
        ]
        for goal in goals
    ]
    output.table(
        ["REF", "GOAL", "STATE", "DONE", "TARGET", "OWNER"],
        rows,
        empty="No goals on this project yet.",
    )


def _show(args: argparse.Namespace, ctx: Context) -> None:
    goal = ctx.client.get(f"/goals/{args.goal}")

    if ctx.as_json:
        output.emit_json(goal)
        return

    progress = goal.get("progress") or {}
    output.heading(f"{goal['reference']}  {goal['name']}")
    output.fields(
        [
            ("Status", str(goal["status"])),
            ("Owner", str((goal.get("owner") or {}).get("name", ""))),
            ("Target", str(goal.get("target_date") or "none")),
            ("Colour", str(goal["colour"])),
            ("Cards", _progress(goal)),
            ("Left", str(progress.get("open", 0))),
        ]
    )
    if goal.get("description"):
        output.echo()
        for line in output.wrap(str(goal["description"]), output.terminal_width()):
            output.echo(line)

    tasks = goal.get("tasks") or []
    output.echo()
    output.table(
        ["REF", "CARD", "STATUS", "OWNER"],
        [
            [
                str(task["reference"]),
                output.truncate(str(task["title"]), NAME_WIDTH),
                str(task["status"]),
                str((task.get("assignee") or {}).get("name", "")),
            ]
            for task in tasks
        ],
        empty="No cards on this goal yet.",
    )


def _new(args: argparse.Namespace, ctx: Context) -> None:
    body: dict[str, Any] = {
        "name": args.name,
        "description": args.description,
        "owner_id": args.owner,
    }
    if args.colour:
        body["colour"] = args.colour
    if args.target:
        body["target_date"] = args.target

    goal = ctx.client.post(f"/projects/{args.project}/goals", body)
    if ctx.as_json:
        output.emit_json(goal)
        return
    output.echo(f"Started {goal['reference']}: {goal['name']}.")


def _status(args: argparse.Namespace, ctx: Context) -> None:
    goal = ctx.client.patch(f"/goals/{args.goal}", {"status": args.state})
    if ctx.as_json:
        output.emit_json(goal)
        return
    output.echo(f"{goal['reference']} is {goal['status']}.")


def _link(args: argparse.Namespace, ctx: Context) -> None:
    goal = ctx.client.get(f"/goals/{args.goal}")
    task = ctx.client.patch(f"/tasks/{args.task}", {"goal_id": goal["id"]})
    if ctx.as_json:
        output.emit_json(task)
        return
    output.echo(f"{task['reference']} is on {goal['reference']} ({goal['name']}).")


def _unlink(args: argparse.Namespace, ctx: Context) -> None:
    task = ctx.client.patch(f"/tasks/{args.task}", {"goal_id": None})
    if ctx.as_json:
        output.emit_json(task)
        return
    output.echo(f"{task['reference']} is on no goal. It is still on the board.")


def _progress(goal: dict[str, Any]) -> str:
    """Done over total — "3/8" — which is how a goal is read at a glance."""
    progress = goal.get("progress") or {}
    return f"{progress.get('done', 0)}/{progress.get('total', 0)}"
