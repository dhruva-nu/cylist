"""Projects: the keys every other tool takes, and a board's columns."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp.client import Api
from cylist_mcp.tools.common import ProjectRef, as_tool_result


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_projects",
        description=(
            "List every project, newest first. Use this to discover the project "
            "keys (like 'ATL') that every other tool takes. Returns each "
            "project's key, name, description, colour, member count and whether "
            "it is archived."
        ),
    )
    async def list_projects(
        include_archived: Annotated[
            bool, Field(description="Include archived projects. Default false.")
        ] = False,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            projects = await client.get("/projects", include_archived=include_archived or None)
            return {"projects": projects}

        return await as_tool_result(call)

    @server.tool(
        name="get_project",
        description=(
            "Get one project with its headline numbers and its board's columns. "
            "Returns the project's counts (tasks, blocked, on hold, files, vault "
            "secrets, team and client members) and the ordered list of columns "
            "with their names and ids. Call this before move_task so you know "
            "what the board's columns are called."
        ),
    )
    async def get_project(project: ProjectRef) -> CallToolResult:
        async def call() -> dict[str, Any]:
            summary = await client.get(f"/projects/{project}/summary")
            board = await client.get(f"/projects/{project}/columns")
            return {"project": summary, "columns": board.get("columns", [])}

        return await as_tool_result(call)
