"""People: the one directory, and each project's members out of it."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp.client import Api, every
from cylist_mcp.tools.common import as_tool_result, check_choice

PERSON_KINDS = ("team", "client")


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_people",
        description=(
            "List people. Cylist keeps one global directory; 'team' does the "
            "work and 'client' approves or unblocks it. Pass project to list "
            "only that project's members — which is the set an assignee or a "
            "waiting_on tag must come from. Returns each person's name, kind, "
            "title, responsibilities and email. Title is their job description "
            "('Finance controller, Atlas'); a project's members also carry "
            "role, which is what they are on that board and is null until an "
            "admin has said. One entry carries is_agent: 'Agent' is the board's "
            "own machine, on every project, and is who to assign a card to when "
            "the work is meant for one."
        ),
    )
    async def list_people(
        project: Annotated[
            str | None, Field(description="Only this project's members, by key or id.")
        ] = None,
        kind: Annotated[str | None, Field(description="Filter to 'team' or 'client'.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if kind is not None:
                check_choice("kind", kind, PERSON_KINDS)
            if project:
                people = (await client.get(f"/projects/{project}/members")).get("members", [])
                if kind:
                    people = [person for person in people if person.get("kind") == kind]
            else:
                people = await every(client, "/people", kind=kind)
            return {"people": people}

        return await as_tool_result(call)
