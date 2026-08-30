"""``cylist files ls`` and ``cylist files get``."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

from cylist_cli import output, resolve
from cylist_cli.context import Context
from cylist_cli.errors import CylistError

NAME_WIDTH = 40
KILOBYTE = 1024.0


def register(subparsers: Any) -> None:
    files = subparsers.add_parser("files", help="A project's folders, uploads and links.")
    actions = files.add_subparsers(dest="action", required=True, metavar="<action>")

    ls = actions.add_parser(
        "ls",
        help="List a folder.",
        description=(
            "With no path, lists the project's top-level folders. A project "
            "has no root folder of its own, so files always live at least one "
            "level down."
        ),
    )
    ls.add_argument("project", metavar="PROJECT")
    ls.add_argument("path", nargs="?", default="", metavar="PATH", help="e.g. Contracts/2026")
    ls.set_defaults(handler=_list)

    get = actions.add_parser("get", help="Download a file.")
    get.add_argument("project", metavar="PROJECT")
    get.add_argument("path", metavar="PATH", help="e.g. Contracts/2026/msa.pdf")
    get.add_argument(
        "-o",
        "--output",
        metavar="FILE",
        help="Where to write it. Defaults to the file's own name; '-' means stdout.",
    )
    get.set_defaults(handler=_get)


def _list(args: argparse.Namespace, ctx: Context) -> None:
    if not args.path:
        tree = ctx.client.get(f"/projects/{args.project}/tree")
        folders = [{"id": node["id"], "name": node["name"]} for node in tree]
        if ctx.as_json:
            output.emit_json({"folders": folders, "items": []})
            return
        output.table(
            ["KIND", "NAME", "SIZE", "SOURCE"],
            [["folder", str(folder["name"]), "", ""] for folder in folders],
            empty="This project has no folders yet.",
        )
        return

    folder = resolve.folder(ctx.client, args.project, args.path)
    listing = ctx.client.get(f"/folders/{folder['id']}/children")

    if ctx.as_json:
        output.emit_json(listing)
        return

    rows = [
        ["folder", output.truncate(str(sub["name"]), NAME_WIDTH), "", ""]
        for sub in listing.get("folders", [])
    ]
    rows.extend(
        [
            str(item["kind"]),
            output.truncate(str(item["name"]), NAME_WIDTH),
            _size(item.get("size")),
            str(item.get("url") or item.get("source") or ""),
        ]
        for item in listing.get("items", [])
    )
    output.table(["KIND", "NAME", "SIZE", "SOURCE"], rows, empty="This folder is empty.")


def _size(value: Any) -> str:
    if not isinstance(value, int):
        return ""
    size = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if size < KILOBYTE or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= KILOBYTE
    return f"{size:.1f}GB"


def _get(args: argparse.Namespace, ctx: Context) -> None:
    item = resolve.item(ctx.client, args.project, args.path)

    if item.get("kind") == "link":
        raise CylistError(
            f"{item['name']!r} is a link, not an upload. Its content is at {item.get('url')}.",
            details={"url": item.get("url")},
        )

    destination = args.output or str(item["name"])
    chunks = ctx.client.stream(f"/items/{item['id']}/download")

    if destination == "-":
        # Straight to the byte stream: a PDF must not go through text encoding.
        for chunk in chunks:
            sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
        return

    target = Path(destination)
    written = 0
    try:
        with target.open("wb") as handle:
            for chunk in chunks:
                written += handle.write(chunk)
    except OSError as exc:
        raise CylistError(f"Cannot write {target}: {exc.strerror}.") from exc

    if ctx.as_json:
        output.emit_json({"path": str(target), "bytes": written, "item": item})
        return
    output.echo(f"Wrote {written} bytes to {target}.")
