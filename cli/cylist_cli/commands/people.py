"""``cylist people …`` — the global directory."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output
from cylist_cli.context import Context

KINDS = ("team", "client")
TITLE_WIDTH = 38
"""How much of a job title a row shows before it is cut.

The titles that run long — "Finance controller, Atlas" — say the useful half
first, so a cut here loses the qualifier rather than the job."""


def register(subparsers: Any) -> None:
    people = subparsers.add_parser("people", help="The people directory.")
    actions = people.add_subparsers(dest="action", required=True, metavar="<action>")

    ls = actions.add_parser("ls", help="List people.")
    ls.add_argument("--kind", choices=KINDS, help="Only team members, or only clients.")
    ls.add_argument(
        "--project", metavar="PROJECT", help="Only the people on this project, by key or id."
    )
    ls.add_argument("--all", action="store_true", help="Include archived people.")
    ls.set_defaults(handler=_list)

    new = actions.add_parser("new", help="Add someone to the directory.")
    new.add_argument("--name", required=True)
    new.add_argument("--kind", required=True, choices=KINDS)
    new.add_argument("--title", required=True, help="Who they are, in one line.")
    new.add_argument(
        "--responsibilities", required=True, help="What they do, and so what to tag them about."
    )
    new.add_argument("--email")
    new.add_argument("--colour", help="Six-digit hex. Omit to take one from the palette.")
    new.set_defaults(handler=_new)


def _list(args: argparse.Namespace, ctx: Context) -> None:
    if args.project:
        people = ctx.client.get(f"/projects/{args.project}/members").get("members", [])
        if args.kind:
            people = [person for person in people if person.get("kind") == args.kind]
    else:
        people = ctx.client.get(
            "/people",
            kind=args.kind,
            include_archived=True if args.all else None,
        )

    if ctx.as_json:
        output.emit_json(people)
        return

    # ROLE is only asked for when the listing is a project's, because that is
    # the only place a person has one: a role belongs to a board, not to the
    # directory. TITLE is the job description and is always there.
    on_a_project = bool(args.project)
    rows = [
        [
            str(person["name"]),
            _kind_of(person),
            output.truncate(str(person.get("title", "")), TITLE_WIDTH),
            *([_role_of(person)] if on_a_project else []),
            str(person.get("email") or ""),
        ]
        for person in people
    ]
    headings = ["NAME", "KIND", "TITLE", *(["ROLE"] if on_a_project else []), "EMAIL"]
    output.table(headings, rows, empty="Nobody in the directory yet.")


def _kind_of(person: dict[str, Any]) -> str:
    """Which side of the work they are on — or that they are not a person.

    The agent is on the team, because it does the work, so the kind column
    alone would show it as a colleague with no email. It is the one row in the
    directory worth saying more about than its kind.
    """
    if person.get("is_agent"):
        return "agent"
    return str(person["kind"])


def _role_of(person: dict[str, Any]) -> str:
    """What this person is on the project, or nothing if nobody has said."""
    role = person.get("role")
    return str(role["name"]) if isinstance(role, dict) else ""


def _new(args: argparse.Namespace, ctx: Context) -> None:
    body: dict[str, Any] = {
        "name": args.name,
        "kind": args.kind,
        "title": args.title,
        "responsibilities": args.responsibilities,
    }
    if args.email:
        body["email"] = args.email
    if args.colour:
        body["colour"] = args.colour

    person = ctx.client.post("/people", body)
    if ctx.as_json:
        output.emit_json(person)
        return
    output.echo(f"Added {person['name']} ({person['kind']}).")
