"""The audit feed, and the day report cut from it."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp.client import Api
from cylist_mcp.tools.common import ProjectRef, as_tool_result


def register(server: MCPServer, client: Api, *, hosted: bool) -> None:
    """Add ``read_activity`` and ``day_report``.

    ``hosted`` decides which zone a day report is cut by when none is given.
    Beside the caller, this machine's zone is the right guess; on the server it
    is the container's, which is an accident of how the image was built, so the
    API's own UTC is the more honest default.
    """
    default_zone: Callable[[], str | None] = no_timezone if hosted else local_timezone
    zone_default_text = (
        "defaults to UTC, because this server does not run beside you — pass the user's own zone"
        if hosted
        else "defaults to this machine's own zone rather than UTC"
    )

    @server.tool(
        name="read_activity",
        description=(
            "Read the audit feed, newest first. Every mutation anyone makes is "
            "recorded with who did it and whether it came through the web or "
            "the API, so this answers 'what changed, and was it a person or an "
            "agent?'. Filter by project or by entity type ('task', 'project', "
            "'vault_node', 'token')."
        ),
    )
    async def read_activity(
        project: Annotated[
            str | None, Field(description="Limit to one project, by key or id.")
        ] = None,
        entity_type: Annotated[
            str | None, Field(description="e.g. 'task', 'project', 'vault_node'.")
        ] = None,
        limit: Annotated[
            int, Field(description="How many entries. Default 20, maximum 200.", ge=1, le=200)
        ] = 20,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            entries = await client.get(
                "/activity", project=project, entity_type=entity_type, limit=limit
            )
            return {"activity": entries}

        return await as_tool_result(call)

    @server.tool(
        name="day_report",
        description=(
            "Report what was done on one project on one day: every card that was "
            "touched, what happened to it in order, which cards ended the day in "
            "the board's last column, and what happened away from the board — files, "
            "columns, the vault. This is the tool for 'what did I do today', a "
            "stand-up note, or a end-of-day summary. The reply carries 'markdown', "
            "the whole report already worded and ready to paste; prefer quoting "
            "that over rewriting it from the structured fields, so the note reads "
            "the same however it was asked for. A day means midnight to midnight "
            f"in 'timezone', which {zone_default_text}. "
            "The report is deliberately concise: a card's moves appear as the "
            "one move they amounted to, from the column the day started in to the "
            "one it ended in, and a comment's line quotes what was said. An entry "
            "whose channel is 'api' was an agent's work, not the person's."
        ),
    )
    async def day_report(
        project: ProjectRef,
        date: Annotated[
            str | None,
            Field(description="Which day, as 'YYYY-MM-DD'. Defaults to today in `timezone`."),
        ] = None,
        timezone: Annotated[
            str | None,
            Field(
                description=(
                    "IANA zone the day is cut by, e.g. 'Asia/Kolkata'. Omit it for "
                    "the default the tool's description names."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            report = await client.get(
                f"/projects/{project}/reports/day",
                date=date,
                timezone=timezone or default_zone(),
            )
            return {"report": report}

        return await as_tool_result(call)


def no_timezone() -> None:
    """No zone at all, which the API takes as UTC."""
    return None


def local_timezone() -> str | None:
    """The IANA zone this machine is set to, or None if it cannot be named.

    A day report is cut at local midnight, and the local day is the one the
    person asking has just lived — this server runs beside them, so its own
    zone is a far better default than the API's UTC.

    Asked of the system rather than of ``datetime``, because what is needed is
    a *name*: ``now().astimezone().tzname()`` gives "IST", which no zone
    database can be looked up by. ``TZ`` first, since that is what anything
    setting a zone deliberately sets; then the symlink Linux and macOS keep at
    ``/etc/localtime``. When neither answers, None lets the server apply its
    own default rather than this guessing one.
    """
    configured = os.environ.get("TZ", "").lstrip(":").strip()
    if configured:
        return configured

    try:
        parts = Path("/etc/localtime").resolve().parts
    except OSError:  # pragma: no cover - an unreadable /etc is not worth faking
        return None
    if "zoneinfo" in parts:
        return "/".join(parts[parts.index("zoneinfo") + 1 :])
    return None
