"""``cylist board ATL`` — the Kanban board, in a terminal."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output
from cylist_cli.context import Context

GUTTER = "  "
MIN_COLUMN = 20
MAX_COLUMN = 32

STATUS_MARK = {"active": "", "hold": "[hold] ", "blocked": "[blocked] "}
"""Words, not colour. A board pasted into a ticket keeps its meaning."""

AGENT_MARK = {
    "working": "agent: working",
    "waiting": "agent: needs you",
    "done": "agent: finished",
}
"""What the web board draws as the card's border, said in words.

Its own line under the reference rather than a prefix on it: a column here is
thirty-two characters at its widest, and a prefix long enough to say which of
these states this is would push the reference off the end of it.
"""


def register(subparsers: Any) -> None:
    board = subparsers.add_parser(
        "board",
        help="Show a project's board as columns.",
        description=(
            "Columns side by side, left to right, exactly as the web board "
            "shows them. Falls back to one column per section when the "
            "terminal is too narrow to give each a readable width."
        ),
    )
    board.add_argument("project", metavar="PROJECT", help="Project key or id, e.g. ATL.")
    board.add_argument(
        "--limit",
        type=int,
        metavar="N",
        help="Cards to show from the top of each column (at most 100; the server's default "
        "otherwise). A column holding more says how many more.",
    )
    board.set_defaults(handler=_board)


def _board(args: argparse.Namespace, ctx: Context) -> None:
    columns = ctx.client.get(f"/projects/{args.project}/columns").get("columns", [])
    # The top of each column rather than the whole board: a column can hold
    # thousands of cards, and a terminal shows a screenful.
    board = ctx.client.get(f"/projects/{args.project}/tasks", limit=args.limit)

    if ctx.as_json:
        output.emit_json({"columns": columns, "tasks": board})
        return

    grouped: dict[str, list[dict[str, Any]]] = {str(column["id"]): [] for column in columns}
    hidden: dict[str, int] = {}
    for stack in board.get("columns", []):
        shown = list(stack.get("items", []))
        grouped[str(stack["column_id"])] = shown
        hidden[str(stack["column_id"])] = max(0, int(stack.get("total", 0)) - len(shown))

    if not columns:
        output.echo("This board has no columns.")
        return

    width = output.terminal_width()
    per_column = (width - len(GUTTER) * (len(columns) - 1)) // len(columns)

    if per_column < MIN_COLUMN:
        _stacked(columns, grouped, hidden, width)
    else:
        _side_by_side(columns, grouped, hidden, min(per_column, MAX_COLUMN))


def _more(count: int) -> str:
    """The line under a column that is longer than what was fetched of it."""
    return f"… {count} more"


def _card_lines(task: dict[str, Any], width: int) -> list[str]:
    """One card: its reference, its title wrapped, and who has it."""
    mark = STATUS_MARK.get(str(task.get("status")), "")
    lines = [output.truncate(f"{mark}{task['reference']}", width)]
    agent = AGENT_MARK.get(str((task.get("agent_session") or {}).get("state")), "")
    if agent:
        lines.append(output.truncate(agent, width))
    lines.extend(output.wrap(str(task["title"]), width))
    assignee = task.get("assignee") or {}
    if assignee.get("name"):
        lines.append(output.truncate(f"— {assignee['name']}", width))
    return lines


def _side_by_side(
    columns: list[dict[str, Any]],
    grouped: dict[str, list[dict[str, Any]]],
    hidden: dict[str, int],
    width: int,
) -> None:
    headers: list[list[str]] = []
    bodies: list[list[str]] = []

    for column in columns:
        tasks = grouped.get(str(column["id"]), [])
        headers.append(
            [
                output.truncate(str(column["name"]).upper(), width),
                "-" * width,
            ]
        )
        body: list[str] = []
        for task in tasks:
            body.extend(_card_lines(task, width))
            body.append("")
        if hidden.get(str(column["id"])):
            body.append(output.truncate(_more(hidden[str(column["id"])]), width))
        if not tasks:
            body.append("(empty)")
        bodies.append(body)

    for row in _zip_columns(headers, width):
        output.echo(row)
    for row in _zip_columns(bodies, width):
        output.echo(row)


def _zip_columns(blocks: list[list[str]], width: int) -> list[str]:
    """Lay parallel blocks of text out as columns of equal width."""
    height = max((len(block) for block in blocks), default=0)
    rows: list[str] = []
    for index in range(height):
        cells = [(block[index] if index < len(block) else "").ljust(width) for block in blocks]
        rows.append(GUTTER.join(cells).rstrip())
    return rows


def _stacked(
    columns: list[dict[str, Any]],
    grouped: dict[str, list[dict[str, Any]]],
    hidden: dict[str, int],
    width: int,
) -> None:
    """The narrow-terminal rendering: each column as its own section."""
    for index, column in enumerate(columns):
        if index:
            output.echo()
        tasks = grouped.get(str(column["id"]), [])
        more = hidden.get(str(column["id"]), 0)
        output.echo(f"{str(column['name']).upper()} ({len(tasks) + more})")
        output.echo("-" * min(width, 40))
        if not tasks:
            output.echo("(empty)")
            continue
        for task in tasks:
            for line in _card_lines(task, width - 2):
                output.echo(f"  {line}" if line else "")
        if more:
            output.echo(f"  {_more(more)}")
