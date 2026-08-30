"""``cylist people …`` — the global directory."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output
from cylist_cli.context import Context

KINDS = ("team", "client")
ROLE_WIDTH = 38


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
    new.add_argument("--role", required=True, help="Who they are, in one line.")
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

    rows = [
        [
            str(person["name"]),
            str(person["kind"]),
            output.truncate(str(person.get("role", "")), ROLE_WIDTH),
            str(person.get("email") or ""),
        ]
        for person in people
    ]
    output.table(["NAME", "KIND", "ROLE", "EMAIL"], rows, empty="Nobody in the directory yet.")


def _new(args: argparse.Namespace, ctx: Context) -> None:
    body: dict[str, Any] = {
        "name": args.name,
        "kind": args.kind,
        "role": args.role,
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
