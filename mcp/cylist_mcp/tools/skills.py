"""Skills: the procedures somebody has already written down for a project's agents."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp.client import Api
from cylist_mcp.errors import CylistError
from cylist_mcp.tools.common import ProjectRef, as_tool_result

SKILL_MAX_CHARS = 40_000
"""How much of a skill `read_skill` will return. A skill is a page of
instructions; anything past this is not one, and filling a context window
with it would be the wrong failure."""


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_skills",
        description=(
            "List the skills uploaded for a project's agents — packaged jobs "
            "you can be handed, such as tidying a board or writing a day "
            "report. Returns each one's name, description and size, not its "
            "content; read_skill fetches that, and download_skill gives you one "
            "to install as a Claude Code skill of your own. A skill is often a "
            "whole folder — a SKILL.md beside the scripts and references it "
            "uses — and is listed as '<folder>.zip'. Worth calling before you "
            "improvise a procedure that somebody has already written down."
        ),
    )
    async def list_skills(project: ProjectRef) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"skills": await client.get(f"/projects/{project}/skills")}

        return await as_tool_result(call)

    @server.tool(
        name="read_skill",
        description=(
            "Read one skill's own text, by the name list_skills gave. Skills "
            "are usually markdown: instructions written for you to follow. A "
            "skill that is a folder — listed as '<folder>.zip' — is read as "
            "its SKILL.md, with the other files it carries listed beside it; "
            "download_skill is what gets you those. "
            f"Truncated past {SKILL_MAX_CHARS} characters, which is said in "
            "the result when it happens."
        ),
    )
    async def read_skill(
        project: ProjectRef,
        name: Annotated[str, Field(description="The skill's name, e.g. 'board-tidy.md'.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            skill = await find_skill(client, project, name)
            if not is_zip(skill):
                text, truncated = await client.get_text(
                    f"/skills/{skill['id']}/download", max_chars=SKILL_MAX_CHARS
                )
                return {"skill": skill, "content": text, "truncated": truncated}

            # The bytes of a zip are no use to a model. What it wants is the
            # instructions, which are SKILL.md once the server has unpacked it.
            folder = await client.get(f"/skills/{skill['id']}/folder")
            files = folder.get("files", [])
            text = skill_md(files)
            return {
                "skill": skill,
                "content": text[:SKILL_MAX_CHARS],
                "truncated": len(text) > SKILL_MAX_CHARS,
                "files": [entry.get("path") for entry in files],
            }

        return await as_tool_result(call)

    @server.tool(
        name="download_skill",
        description=(
            "Get one skill as the folder Claude Code loads skills from, so you "
            "can install it and use it rather than only read it. Returns the "
            "folder's name and every file in it — SKILL.md first, a zip "
            "unpacked, base64 for anything that is not text — and how to "
            "install it. The simplest way is to run the `command` it gives "
            "(`cylist skills pull ...`), which writes .claude/skills/<folder>/ "
            "in the repository you are in and will not overwrite a skill it did "
            "not write; without the cylist CLI, write each file under that "
            "directory yourself. A running session picks the skill up within "
            "seconds if .claude/skills already existed when it started, and "
            "from the next session otherwise; until then, follow its SKILL.md "
            "from disk. To put a skill the other way, onto the board, run "
            "`cylist skills push <project> <directory>`: it uploads the whole "
            "folder in one go and there is no tool for it here."
        ),
    )
    async def download_skill(
        project: ProjectRef,
        name: Annotated[
            str,
            Field(description="The skill's name as list_skills gave it, e.g. 'cylist.zip'."),
        ],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            skill = await find_skill(client, project, name)
            folder = await client.get(f"/skills/{skill['id']}/folder")
            directory = f".claude/skills/{folder.get('folder')}/"
            return {
                **folder,
                "install": {
                    "command": f"cylist skills pull {project} {skill['name']}",
                    "directory": directory,
                },
            }

        return await as_tool_result(call)


async def find_skill(client: Api, project: str, name: str) -> dict[str, Any]:
    """The skill of that name on the project, or an error listing the ones it has."""
    skills = await client.get(f"/projects/{project}/skills")
    for skill in skills:
        if skill.get("name") == name:
            return dict(skill)

    available = sorted(str(skill.get("name")) for skill in skills)
    what_it_has = f"It has: {', '.join(available)}." if available else "It has none."
    raise CylistError(
        f"{project} has no skill called {name!r}. {what_it_has}",
        code="not_found",
        details={"name": name, "available": available},
    )


def is_zip(skill: dict[str, Any]) -> bool:
    named_as_zip = str(skill.get("name", "")).lower().endswith(".zip")
    typed_as_zip = "zip" in str(skill.get("mime", ""))
    return named_as_zip or typed_as_zip


def skill_md(files: list[dict[str, Any]]) -> str:
    """The text of the SKILL.md among an unpacked skill's files, or ``""``."""
    for entry in files:
        if entry.get("path") == "SKILL.md":
            return str(entry["content"])
    return ""
