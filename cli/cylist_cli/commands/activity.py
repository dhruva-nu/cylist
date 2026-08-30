"""``cylist activity`` — the audit feed."""

from __future__ import annotations

import argparse
from typing import Any

from cylist_cli import output
from cylist_cli.context import Context

PAYLOAD_WIDTH = 40


def register(subparsers: Any) -> None:
    activity = subparsers.add_parser(
        "activity",
        help="Recent activity, newest first.",
        description=(
            "Every mutation is recorded with who did it and whether it came "
            "through the web or the API, which is how an agent's work stays "
            "auditable after the fact."
        ),
    )
    activity.add_argument(
        "--project", metavar="PROJECT", help="Limit to one project, by key or id."
    )
    activity.add_argument("--entity", metavar="TYPE", help="e.g. task, project, vault_node.")
    activity.add_argument("--limit", type=int, default=20, help="Default 20, maximum 200.")
    activity.set_defaults(handler=_activity)


def _activity(args: argparse.Namespace, ctx: Context) -> None:
    project_id = None
    if args.project:
        # The feed filters by project *id* only, so a key has to be resolved
        # first — the one endpoint in the API that does not take {project_ref}.
        project_id = str(ctx.client.get(f"/projects/{args.project}")["id"])

    entries = ctx.client.get(
        "/activity", project_id=project_id, entity_type=args.entity, limit=args.limit
    )

    if ctx.as_json:
        output.emit_json(entries)
        return

    rows = [
        [
            str(entry.get("occurred_at", ""))[:19].replace("T", " "),
            str(entry.get("actor_label", "")),
            str(entry.get("channel", "")),
            str(entry.get("verb", "")),
            output.truncate(_summarise(entry.get("payload") or {}), PAYLOAD_WIDTH),
        ]
        for entry in entries
    ]
    output.table(["WHEN", "WHO", "VIA", "WHAT", "DETAIL"], rows, empty="Nothing recorded yet.")


def _summarise(payload: dict[str, Any]) -> str:
    """Pick the fields worth a column out of a free-form payload."""
    for key in ("reference", "key", "name", "title"):
        if payload.get(key):
            return str(payload[key])
    return ", ".join(f"{key}={value}" for key, value in list(payload.items())[:2])
