"""``cylist vault …`` — structure freely, plaintext only when asked twice.

The rule this module is built around: **a secret is never printed as a side
effect of any command.** ``reveal`` without ``--show`` or ``-o`` prints the
credential's metadata and tells you how to ask for the value. That is not
friction for its own sake — a revealed secret lands in scrollback, in ``script``
logs, in a CI job's captured output and in whatever the terminal multiplexer is
buffering, and none of those are places anyone decided to put it.

The secret itself never travels as an argument either, in this direction or the
other: ``vault add`` reads it from a prompt or from stdin, so it stays out of
shell history and out of ``ps``.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path
from typing import Any

from cylist_cli import output, resolve
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

SECRET_FILE_MODE = 0o600


def register(subparsers: Any) -> None:
    vault = subparsers.add_parser("vault", help="A project's stored credentials.")
    actions = vault.add_subparsers(dest="action", required=True, metavar="<action>")

    ls = actions.add_parser(
        "ls",
        help="List vault trees, or one tree's contents.",
        description="Never shows a secret value; that is what 'reveal' is for.",
    )
    ls.add_argument("project", metavar="PROJECT")
    ls.add_argument("path", nargs="?", default="", metavar="PATH", help="e.g. Logins/Billing")
    ls.set_defaults(handler=_list)

    reveal = actions.add_parser(
        "reveal",
        help="Decrypt one credential. Requires the vault:reveal scope.",
        description=(
            "Prints the secret only when you say where it should go: --show "
            "writes it to stdout, -o writes it to a file created 0600. "
            "Without either, only the metadata is shown. Every reveal is "
            "recorded in the activity feed."
        ),
    )
    reveal.add_argument("project", metavar="PROJECT")
    reveal.add_argument("path", metavar="PATH", help="e.g. Logins/Billing/Stripe")
    reveal.add_argument(
        "--show", action="store_true", help="Print the value to stdout. It will be in scrollback."
    )
    reveal.add_argument(
        "-o", "--output", metavar="FILE", help="Write the value to FILE, mode 0600."
    )
    reveal.set_defaults(handler=_reveal)

    add = actions.add_parser(
        "add",
        help="Store a new credential.",
        description=(
            "The value is read from a hidden prompt, or from stdin with "
            "--value-stdin. It is never taken from an argument."
        ),
    )
    add.add_argument("project", metavar="PROJECT")
    add.add_argument("path", metavar="PATH", help="e.g. Logins/Billing/Stripe")
    add.add_argument("--username")
    add.add_argument("--url")
    add.add_argument("--notes", default="")
    add.add_argument("--value-stdin", action="store_true", help="Read the value from stdin.")
    add.set_defaults(handler=_add)

    tree = actions.add_parser(
        "add-tree",
        help="Start a new vault tree.",
        description=(
            "A tree is the top level of a project's vault — 'Logins', "
            "'Infrastructure'. Secrets live in one, so a project needs at "
            "least one before 'vault add' has anywhere to put anything."
        ),
    )
    tree.add_argument("project", metavar="PROJECT")
    tree.add_argument("name", metavar="NAME", help="e.g. Logins")
    tree.set_defaults(handler=_add_tree)


# --- Listing ---------------------------------------------------------------


def _list(args: argparse.Namespace, ctx: Context) -> None:
    if not args.path:
        trees = ctx.client.get(f"/projects/{args.project}/vault/trees")
        if ctx.as_json:
            output.emit_json(trees)
            return
        rows = [
            [str(tree["name"]), str(tree["node_count"]), str(tree["secret_count"])]
            for tree in trees
        ]
        output.table(["TREE", "NODES", "SECRETS"], rows, empty="This project has no vault trees.")
        return

    segments = resolve.split_path(args.path)
    if len(segments) == 1:
        trees = ctx.client.get(f"/projects/{args.project}/vault/trees")
        summary = resolve.pick(trees, segments[0], kind="vault tree", where=args.project)
        tree = ctx.client.get(f"/vault/trees/{summary['id']}")
        nodes = tree.get("nodes", [])
        title = str(tree["name"])
    else:
        _, node = resolve.vault_node(ctx.client, args.project, args.path)
        nodes = [node]
        title = "/".join(segments)

    if ctx.as_json:
        output.emit_json(nodes)
        return

    output.echo(title)
    for line in _tree_lines(nodes, ""):
        output.echo(line)


def _tree_lines(nodes: list[dict[str, Any]], indent: str) -> list[str]:
    """Render a vault subtree, marking secrets and never showing a value."""
    lines: list[str] = []
    for index, node in enumerate(nodes):
        last = index == len(nodes) - 1
        elbow = "`-- " if last else "|-- "
        label = str(node["name"])
        if node.get("kind") == "secret":
            secret = node.get("secret") or {}
            username = secret.get("username")
            label += f"  (secret{f', {username}' if username else ''})"
        lines.append(f"{indent}{elbow}{label}")
        children = node.get("children") or []
        if children:
            lines.extend(_tree_lines(children, indent + ("    " if last else "|   ")))
    return lines


# --- Reveal ----------------------------------------------------------------


def _reveal(args: argparse.Namespace, ctx: Context) -> None:
    tree, node = resolve.vault_node(ctx.client, args.project, args.path)

    if node.get("kind") != "secret":
        raise CylistError(
            f"{node['name']!r} is a branch in {tree['name']!r}, so it holds no credential."
        )

    if not args.show and not args.output:
        # Nothing is fetched: no point logging a reveal in the audit feed for a
        # call that was never going to hand the value over.
        secret = node.get("secret") or {}
        output.echo(f"{tree['name']}/{node['name']}")
        output.fields(
            [
                ("Username", str(secret.get("username") or "")),
                ("URL", str(secret.get("url") or "")),
                ("Notes", str(secret.get("notes") or "")),
                ("Updated", str(secret.get("updated_at") or "")),
            ]
        )
        output.echo()
        output.echo("The value was not fetched. Add --show to print it, or -o FILE to save it.")
        return

    revealed = ctx.client.post(f"/vault/nodes/{node['id']}/reveal")
    value = str(revealed["value"])

    if args.output:
        target = _write_secret(Path(args.output), value)
        output.warn(f"Wrote the secret to {target} (mode 0600). Delete it when you are done.")
        if ctx.as_json:
            output.emit_json({k: v for k, v in revealed.items() if k != "value"})
        return

    output.warn("Revealed — this value is now in your terminal's scrollback, and in the audit log.")
    if ctx.as_json:
        output.emit_json(revealed)
        return
    print(value)


def _write_secret(target: Path, value: str) -> Path:
    """Write a credential to a file that is 0600 from the instant it exists."""
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, SECRET_FILE_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(value)
        finally:
            os.chmod(target, SECRET_FILE_MODE)  # noqa: PTH101 - target is already a Path
    except OSError as exc:
        raise CylistError(f"Cannot write {target}: {exc.strerror}.") from exc
    return target


# --- Adding ----------------------------------------------------------------


def _add(args: argparse.Namespace, ctx: Context) -> None:
    segments = resolve.split_path(args.path)
    if len(segments) < 2:
        raise CylistError(f"{args.path!r} needs at least a tree and a name, e.g. 'Logins/Stripe'.")

    trees = ctx.client.get(f"/projects/{args.project}/vault/trees")
    tree = resolve.pick(trees, segments[0], kind="vault tree", where=args.project)

    parent_id = None
    if len(segments) > 2:
        _, parent = resolve.vault_node(ctx.client, args.project, "/".join(segments[:-1]))
        if parent.get("kind") != "branch":
            raise CylistError(f"{parent['name']!r} is a secret; a secret cannot hold another.")
        parent_id = str(parent["id"])

    value = sys.stdin.readline().rstrip("\n") if args.value_stdin else getpass.getpass("Value: ")
    if not value:
        raise CylistError("No value given; nothing was stored.")

    secret: dict[str, Any] = {"value": value, "notes": args.notes}
    if args.username:
        secret["username"] = args.username
    if args.url:
        secret["url"] = args.url

    node = ctx.client.post(
        "/vault/nodes",
        {
            "tree_id": str(tree["id"]),
            "parent_id": parent_id,
            "name": segments[-1],
            "kind": "secret",
            "secret": secret,
        },
    )

    if ctx.as_json:
        output.emit_json(node)
        return
    output.echo(f"Stored {tree['name']}/{node['name']}.")


def _add_tree(args: argparse.Namespace, ctx: Context) -> None:
    tree = ctx.client.post(f"/projects/{args.project}/vault/trees", {"name": args.name})
    if ctx.as_json:
        output.emit_json(tree)
        return
    output.echo(f"Created the vault tree {tree['name']} in {args.project}.")
