"""``cylist projects`` and ``cylist project …``."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output, resolve
from cylist_cli.context import Context

DESCRIPTION_WIDTH = 44


def register(subparsers: Any) -> None:
    listing = subparsers.add_parser("projects", help="List projects.")
    listing.add_argument("action", nargs="?", choices=["ls"], default="ls", help=argparse.SUPPRESS)
    listing.add_argument("--all", action="store_true", help="Include archived projects.")
    listing.set_defaults(handler=_list)

    project = subparsers.add_parser("project", help="Work with one project.")
    actions = project.add_subparsers(dest="action", required=True, metavar="<action>")

    show = actions.add_parser("show", help="Show a project and its headline numbers.")
    show.add_argument("project", metavar="PROJECT", help="Project key or id, e.g. ATL.")
    show.set_defaults(handler=_show)

    new = actions.add_parser("new", help="Start a project.")
    new.add_argument("--key", required=True, help="Short key, e.g. ATL. Becomes ATL-1, ATL-2, …")
    new.add_argument("--name", required=True)
    new.add_argument("--description", default="")
    new.add_argument("--colour", help="Six-digit hex. Omit to take one from the palette.")
    new.set_defaults(handler=_new)

    members = actions.add_parser(
        "members",
        help="Show or change who is on a project.",
        description=(
            "With no flags, lists the members. --add and --remove read the "
            "current list, apply the change and send the result back, because "
            "the API replaces membership wholesale rather than editing it."
        ),
    )
    members.add_argument("project", metavar="PROJECT")
    members.add_argument(
        "--add", action="append", default=[], metavar="NAME", help="Add this person. Repeatable."
    )
    members.add_argument(
        "--remove", action="append", default=[], metavar="NAME", help="Remove. Repeatable."
    )
    members.set_defaults(handler=_members)


def _list(args: argparse.Namespace, ctx: Context) -> None:
    projects = ctx.client.get("/projects", include_archived=True if args.all else None)
    if ctx.as_json:
        output.emit_json(projects)
        return

    rows = [
        [
            str(project["key"]),
            str(project["name"]),
            str(project["member_count"]),
            "archived" if project.get("archived_at") else "active",
            output.truncate(str(project.get("description", "")), DESCRIPTION_WIDTH),
        ]
        for project in projects
    ]
    output.table(
        ["KEY", "NAME", "PEOPLE", "STATE", "DESCRIPTION"],
        rows,
        empty="No projects yet. Start one with 'cylist project new'.",
    )


def _show(args: argparse.Namespace, ctx: Context) -> None:
    summary = ctx.client.get(f"/projects/{args.project}/summary")
    if ctx.as_json:
        output.emit_json(summary)
        return

    output.heading(f"{summary['key']} — {summary['name']}")
    if summary.get("description"):
        output.echo()
        for line in output.wrap(str(summary["description"]), output.terminal_width()):
            output.echo(line)
    output.echo()
    output.fields(
        [
            ("People", f"{summary['team_count']} team, {summary['client_count']} client"),
            (
                "Board",
                f"{summary['task_count']} tasks in {summary['column_count']} columns"
                f" ({summary['blocked_count']} blocked, {summary['on_hold_count']} on hold)",
            ),
            ("Files", f"{summary['file_count']} in {summary['folder_count']} folders"),
            (
                "Vault",
                f"{summary['vault_secret_count']} secrets in {summary['vault_tree_count']} trees",
            ),
            ("Archived", str(summary["archived_at"]) if summary.get("archived_at") else ""),
        ]
    )


def _new(args: argparse.Namespace, ctx: Context) -> None:
    body: dict[str, Any] = {
        "key": args.key,
        "name": args.name,
        "description": args.description,
    }
    if args.colour:
        body["colour"] = args.colour

    project = ctx.client.post("/projects", body)
    if ctx.as_json:
        output.emit_json(project)
        return
    output.echo(f"Created {project['key']} — {project['name']}.")
    output.echo(f"Its board starts with the default columns; see 'cylist board {project['key']}'.")


def _members(args: argparse.Namespace, ctx: Context) -> None:
    current = ctx.client.get(f"/projects/{args.project}/members").get("members", [])

    if not args.add and not args.remove:
        if ctx.as_json:
            output.emit_json({"members": current})
            return
        _print_people(current)
        return

    by_id = {str(person["id"]): person for person in current}

    for name in args.remove:
        person = resolve.pick(current, name, kind="person", where=f"{args.project}'s members")
        by_id.pop(str(person["id"]), None)

    if args.add:
        directory = ctx.client.get("/people")
        for name in args.add:
            person = resolve.pick(directory, name, kind="person", where="the people directory")
            by_id[str(person["id"])] = person

    updated = ctx.client.put(f"/projects/{args.project}/members", {"person_ids": list(by_id)}).get(
        "members", []
    )

    if ctx.as_json:
        output.emit_json({"members": updated})
        return
    output.echo(f"{args.project} now has {len(updated)} member(s).")
    _print_people(updated)


def _print_people(people: list[dict[str, Any]]) -> None:
    rows = [
        [str(person["name"]), str(person["kind"]), str(person.get("role", ""))] for person in people
    ]
    output.table(["NAME", "KIND", "ROLE"], rows, empty="Nobody is on this project yet.")
