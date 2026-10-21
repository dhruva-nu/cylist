"""Files: a project's folders, and links filed in them."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp import resolve
from cylist_mcp.client import Api, every
from cylist_mcp.tools.common import ProjectRef, as_tool_result, check_choice

LINK_SOURCES = ("sharepoint", "gdrive", "other")


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_files",
        description=(
            "List what a project's folders hold. With no path, returns the "
            "top-level folders — a project has no root folder of its own, so "
            "every file is at least one level down. With a path like "
            "'Contracts/2026', returns that folder's subfolders and its items. "
            "An item is either an uploaded file or a link to a document living "
            "in SharePoint, Drive or elsewhere; links carry a url."
        ),
    )
    async def list_files(
        project: ProjectRef,
        path: Annotated[
            str | None, Field(description="Folder path, e.g. 'Contracts/2026'.")
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if not path:
                tree = await client.get(f"/projects/{project}/tree")
                return {"folders": tree, "items": []}
            folder = await resolve.folder_id(client, project, path)
            listing = await client.get(f"/folders/{folder}/children")
            # A folder's items come a page at a time; listing it means all of them.
            items = list(listing.get("items", []))
            if listing.get("next"):
                items.extend(
                    await every(client, f"/folders/{folder}/children", after=listing["next"])
                )
            return {
                "folder": listing.get("folder"),
                "folders": listing.get("folders", []),
                "items": items,
            }

        return await as_tool_result(call)

    @server.tool(
        name="add_link",
        description=(
            "File a link to a document that lives somewhere else — a SharePoint "
            "page, a Drive file, a wiki. The folder must already exist; "
            "list_files shows the tree. This does not upload anything: use it "
            "when the document has a URL, not when you have bytes. Returns the "
            "created item."
        ),
    )
    async def add_link(
        project: ProjectRef,
        folder: Annotated[
            str, Field(description="Folder path, e.g. 'Contracts/2026', or a folder id.")
        ],
        name: Annotated[str, Field(description="What to call it in the listing.")],
        url: Annotated[str, Field(description="An http or https URL.")],
        source: Annotated[
            str,
            Field(description="Where it lives: 'sharepoint', 'gdrive' or 'other'."),
        ] = "other",
        added_by: Annotated[
            str | None, Field(description="Which person added it, by name or id.")
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            check_choice("source", source, LINK_SOURCES)
            folder_id = await resolve.folder_id(client, project, folder)
            body: dict[str, Any] = {"name": name, "url": url, "source": source}
            if added_by:
                body["added_by"] = await resolve.person_id(client, added_by, project_ref=project)
            return {"item": await client.post(f"/folders/{folder_id}/links", body)}

        return await as_tool_result(call)
