"""``cylist tasks ls`` and ``cylist task …`` — the commands an agent lives in."""

from __future__ import annotations

import argparse
from datetime import date
from typing import Any

from cylist_cli import output, resolve
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

TASK_TYPES = ("feature", "bug", "chore")
STATUSES = ("active", "hold", "blocked", "cancelled")
TITLE_WIDTH = 46
VALUE_WIDTH = 44


def register(subparsers: Any) -> None:
    listing = subparsers.add_parser("tasks", help="List tasks on a board.")
    actions = listing.add_subparsers(dest="action", required=True, metavar="<action>")
    ls = actions.add_parser("ls", help="List a project's tasks.")
    ls.add_argument("project", metavar="PROJECT", help="Project key or id, e.g. ATL.")
    ls.add_argument("--status", choices=STATUSES, help="Only tasks in this status.")
    ls.add_argument("--assignee", metavar="NAME", help="Only tasks assigned to this person.")
    ls.add_argument("--column", metavar="NAME", help="Only tasks in this column.")
    ls.set_defaults(handler=_list)

    task = subparsers.add_parser("task", help="Work with one task.")
    task_actions = task.add_subparsers(dest="action", required=True, metavar="<action>")

    show = task_actions.add_parser("show", help="Show a task and its timeline.")
    show.add_argument("task", metavar="TASK", help="Task reference or id, e.g. ATL-41.")
    show.set_defaults(handler=_show)

    history = task_actions.add_parser(
        "history",
        help="Show what has been done to a task.",
        description=(
            "Every change to the card, newest first: what was changed, from "
            "what to what, when, and who changed it. Distinct from 'show', "
            "whose timeline is what people said about the card rather than "
            "what was done to it."
        ),
    )
    history.add_argument("task", metavar="TASK", help="Task reference or id, e.g. ATL-41.")
    history.add_argument("--page", type=int, default=1, help="Which page. Default 1.")
    history.add_argument(
        "--per-page", type=int, default=10, help="Entries per page. Default 10, maximum 100."
    )
    history.set_defaults(handler=_history)

    new = task_actions.add_parser(
        "new",
        help="Add a task.",
        description="The task lands at the bottom of the board's first column.",
    )
    new.add_argument("project", metavar="PROJECT")
    new.add_argument("--title", required=True)
    new.add_argument(
        "--description", required=True, help="What done looks like. The API requires it."
    )
    new.add_argument("--type", required=True, choices=TASK_TYPES)
    new.add_argument("--due", required=True, metavar="YYYY-MM-DD")
    new.add_argument("--assignee", required=True, metavar="NAME", help="A member of the project.")
    new.add_argument("--jira", metavar="REF")
    new.add_argument("--pr", metavar="REF")
    new.set_defaults(handler=_new)

    move = task_actions.add_parser("move", help="Move a task to another column.")
    move.add_argument("task", metavar="TASK")
    move.add_argument("--column", required=True, metavar="NAME", help='e.g. "In progress".')
    move.add_argument(
        "--position", type=int, default=0, help="Index from the top. Default 0. Clamped."
    )
    move.set_defaults(handler=_move)

    status = task_actions.add_parser(
        "status",
        help="Change a task's status.",
        description=(
            "'hold', 'blocked' and 'cancelled' require --reason; the API rejects them "
            "without one. The reason is written to the task's timeline."
        ),
    )
    status.add_argument("task", metavar="TASK")
    status.add_argument("status", metavar="STATUS", choices=STATUSES)
    status.add_argument("--reason", help="Required for hold, blocked and cancelled.")
    status.add_argument(
        "--waiting-on",
        action="append",
        default=[],
        metavar="NAME",
        help="Tag a project member this is waiting on. Repeatable.",
    )
    status.set_defaults(handler=_status)

    comment = task_actions.add_parser("comment", help="Add a comment to a task.")
    comment.add_argument("task", metavar="TASK")
    comment.add_argument("body", metavar="TEXT")
    comment.add_argument(
        "--author", metavar="NAME", help="A project member. Omit to comment as the token."
    )
    comment.set_defaults(handler=_comment)


# --- Listing ---------------------------------------------------------------


def _list(args: argparse.Namespace, ctx: Context) -> None:
    tasks = ctx.client.get(f"/projects/{args.project}/tasks")
    columns = {
        str(column["id"]): str(column["name"])
        for column in ctx.client.get(f"/projects/{args.project}/columns").get("columns", [])
    }

    # Filtering happens here rather than in a query parameter because the API
    # has none: a board is a few hundred cards at most, so it returns all of
    # them in one request and the client narrows. See the README.
    if args.status:
        tasks = [task for task in tasks if task.get("status") == args.status]
    if args.assignee:
        wanted = resolve.person_id(ctx.client, args.assignee, project_ref=args.project)
        tasks = [task for task in tasks if str((task.get("assignee") or {}).get("id")) == wanted]
    if args.column:
        wanted = resolve.column_id(ctx.client, args.project, args.column)
        tasks = [task for task in tasks if str(task.get("column_id")) == wanted]

    if ctx.as_json:
        output.emit_json(tasks)
        return

    rows = [
        [
            str(task["reference"]),
            output.truncate(str(task["title"]), TITLE_WIDTH),
            str(task["type"]),
            str(task["status"]),
            columns.get(str(task["column_id"]), "?"),
            str((task.get("assignee") or {}).get("name", "")),
            str(task["due_date"]),
        ]
        for task in tasks
    ]
    output.table(
        ["REF", "TITLE", "TYPE", "STATUS", "COLUMN", "ASSIGNEE", "DUE"],
        rows,
        empty="No tasks match.",
    )


def _show(args: argparse.Namespace, ctx: Context) -> None:
    task = ctx.client.get(f"/tasks/{args.task}")
    if ctx.as_json:
        output.emit_json(task)
        return
    _render_task(task, ctx)
    _render_timeline(task.get("comments", []), _tagged_names(task, ctx))


def _tagged_names(task: dict[str, Any], ctx: Context) -> dict[str, str]:
    """Map the person ids in a status change's ``meta.tagged`` to names.

    The API stores ids there, which is right for a machine and useless in a
    timeline. One extra request buys every historical tag a name; it is only
    made when the task actually has one.
    """
    if not any((entry.get("meta") or {}).get("tagged") for entry in task.get("comments", [])):
        return {}
    return {str(person["id"]): str(person["name"]) for person in ctx.client.get("/people")}


def _project_of(task: dict[str, Any]) -> str:
    """The project key behind a task, from its reference: 'ATL-41' -> 'ATL'.

    Used in place of ``project_id`` wherever the value may end up in a message.
    Every path that takes ``{project_ref}`` accepts a key, and an error reading
    "no person called 'Le' in ATL's members" is worth having over the same
    sentence with a UUID in it.

    Split from the front: a sub-task's reference carries two numbers —
    ``ATL-41-2`` — and only the key is wanted. Keys cannot contain a dash, so
    the first segment is always it; the id remains the fallback.
    """
    key = str(task.get("reference", "")).split("-", 1)[0]
    return key or str(task["project_id"])


def _render_task(task: dict[str, Any], ctx: Context) -> None:
    width = output.terminal_width()
    output.heading(f"{task['reference']}  {task['title']}")
    output.echo()
    for line in output.wrap(str(task.get("description", "")), width):
        output.echo(line)
    output.echo()

    waiting = ", ".join(person["name"] for person in task.get("waiting_on", []))
    columns = ctx.client.get(f"/projects/{_project_of(task)}/columns").get("columns", [])
    column = next(
        (str(entry["name"]) for entry in columns if str(entry["id"]) == str(task["column_id"])),
        "",
    )

    output.fields(
        [
            ("Status", str(task["status"])),
            ("Column", column),
            ("Type", str(task["type"])),
            ("Due", str(task["due_date"])),
            ("Assignee", str((task.get("assignee") or {}).get("name", ""))),
            ("Waiting on", waiting),
            ("Jira", str(task.get("jira_ref") or "")),
            ("PR", str(task.get("pr_ref") or "")),
            ("Parent", str(task.get("parent_reference") or "")),
        ]
    )
    _render_subtasks(task)


_CHECKLIST_MARKS = {"done": "[x]", "cancelled": "[-]", "open": "[ ]"}


def _render_subtasks(task: dict[str, Any]) -> None:
    """The cards split out of this one, then the tick boxes on it.

    Both are listed even when empty-handed is the answer, because the number
    that matters is how many are still open: while it is above zero the card
    cannot reach the board's last column, and that is worth seeing before the
    move is attempted rather than after it is refused.
    """
    subtasks = task.get("subtasks") or []
    checklist = task.get("checklist") or []
    if not subtasks and not checklist:
        return

    output.echo()
    output.echo("Sub-tasks")
    output.echo("---------")
    for subtask in subtasks:
        output.echo(f"  {subtask['reference']}  {subtask['title']}  ({subtask['status']})")
    for item in checklist:
        mark = _CHECKLIST_MARKS.get(str(item.get("state")), "[ ]")
        output.echo(f"  {mark} {item['title']}")

    open_count = int(task.get("open_subtask_count") or 0)
    if open_count:
        noun = "sub-task" if open_count == 1 else "sub-tasks"
        output.echo()
        output.echo(
            f"  {open_count} open {noun}: finish or cancel each before moving this card "
            "to the last column."
        )


def _render_timeline(comments: list[dict[str, Any]], names: dict[str, str]) -> None:
    if not comments:
        return
    width = output.terminal_width()
    output.echo()
    output.echo("Timeline")
    output.echo("--------")
    for entry in comments:
        author = (entry.get("author") or {}).get("name") or "an agent"
        when = str(entry.get("created_at", ""))[:16].replace("T", " ")
        if entry.get("kind") == "status_change":
            meta = entry.get("meta") or {}
            tagged = ", ".join(names.get(person, person) for person in meta.get("tagged", []) or [])
            headline = f"{when}  {author} set status {meta.get('from')} -> {meta.get('to')}"
            output.echo(headline)
            for line in output.wrap(str(entry.get("body", "")), width - 2, indent="  "):
                output.echo(line)
            if tagged:
                output.echo(f"  waiting on: {tagged}")
        else:
            output.echo(f"{when}  {author}")
            for line in output.wrap(str(entry.get("body", "")), width - 2, indent="  "):
                output.echo(line)
        output.echo()


def _history(args: argparse.Namespace, ctx: Context) -> None:
    page = ctx.client.get(f"/tasks/{args.task}/history", page=args.page, per_page=args.per_page)
    if ctx.as_json:
        output.emit_json(page)
        return

    entries = page.get("entries") or []
    if not entries:
        output.echo(
            "Nothing recorded yet." if not page.get("total") else "No entries on this page."
        )
        return

    width = output.terminal_width()
    for entry in entries:
        when = str(entry.get("occurred_at", ""))[:16].replace("T", " ")
        who = str(entry.get("actor_label", "?"))
        # The channel is spelled out rather than shown as a column: whether an
        # agent or a person did something is the one thing a reader scanning
        # this must not have to decode.
        via = " (agent)" if entry.get("channel") == "api" else ""
        output.echo(f"{when}  {who}{via}")
        for line in output.wrap(str(entry.get("summary", "")), width - 2, indent="  "):
            output.echo(line)
        for change in entry.get("changes") or []:
            was = _value(change.get("from"))
            now = _value(change.get("to"))
            output.echo(f"    {change.get('label')}: {was} -> {now}")
        output.echo()

    # Said even on a single page: without it a reader cannot tell a complete
    # record from the first ten entries of a long one.
    output.echo(f"Page {page.get('page')} of {page.get('pages')} — {page.get('total')} entries.")


def _value(value: Any) -> str:
    """One changed value, short enough to sit on a line with its twin."""
    if value is None or value == "":
        return "(none)"
    text = " / ".join(str(item) for item in value) if isinstance(value, list) else str(value)
    return output.truncate(text, VALUE_WIDTH)


# --- Mutations -------------------------------------------------------------


def _new(args: argparse.Namespace, ctx: Context) -> None:
    body = {
        "title": args.title,
        "description": args.description,
        "type": args.type,
        "due_date": _due(args.due),
        "assignee_id": resolve.person_id(ctx.client, args.assignee, project_ref=args.project),
    }
    if args.jira:
        body["jira_ref"] = args.jira
    if args.pr:
        body["pr_ref"] = args.pr

    task = ctx.client.post(f"/projects/{args.project}/tasks", body)
    if ctx.as_json:
        output.emit_json(task)
        return
    output.echo(f"Created {task['reference']}: {task['title']}")


def _due(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise CylistError(f"--due wants a date like 2026-03-31, not {value!r}.") from exc


def _move(args: argparse.Namespace, ctx: Context) -> None:
    task = ctx.client.get(f"/tasks/{args.task}")
    column = resolve.column_id(ctx.client, _project_of(task), args.column)
    moved = ctx.client.post(
        f"/tasks/{args.task}/move", {"column_id": column, "position": args.position}
    )
    if ctx.as_json:
        output.emit_json(moved)
        return
    output.echo(f"Moved {moved['reference']} to {args.column} at position {moved['position']}.")


def _status(args: argparse.Namespace, ctx: Context) -> None:
    if args.status != "active" and not args.reason:
        raise CylistError(
            f"--reason is required to set a task to {args.status}. "
            "The board records why work stopped, not just that it did."
        )

    task = ctx.client.get(f"/tasks/{args.task}")
    project = _project_of(task)
    body: dict[str, Any] = {
        "status": args.status,
        "waiting_on": [
            resolve.person_id(ctx.client, name, project_ref=project) for name in args.waiting_on
        ],
    }
    if args.reason:
        body["reason"] = args.reason

    updated = ctx.client.post(f"/tasks/{args.task}/status", body)
    if ctx.as_json:
        output.emit_json(updated)
        return

    output.echo(f"{updated['reference']} is now {updated['status']}.")
    waiting = ", ".join(person["name"] for person in updated.get("waiting_on", []))
    if waiting:
        output.echo(f"Waiting on {waiting}.")


def _comment(args: argparse.Namespace, ctx: Context) -> None:
    body: dict[str, Any] = {"body": args.body}
    if args.author:
        task = ctx.client.get(f"/tasks/{args.task}")
        body["author_id"] = resolve.person_id(
            ctx.client, args.author, project_ref=_project_of(task)
        )

    comment = ctx.client.post(f"/tasks/{args.task}/comments", body)
    if ctx.as_json:
        output.emit_json(comment)
        return
    output.echo(f"Commented on {args.task}.")
