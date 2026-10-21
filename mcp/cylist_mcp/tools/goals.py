"""Goals: the epics a project's cards are written under."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp import resolve
from cylist_mcp.client import Api, every
from cylist_mcp.tools.common import ProjectRef, TaskRef, as_tool_result, check_choice, project_of

GOAL_STATUSES = ("open", "achieved", "dropped")

GoalRef = Annotated[str, Field(description="Goal name, reference such as 'ATL-G1', or id.")]


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_goals",
        description=(
            "List a project's goals — the epics its cards are written under. "
            "Each carries its reference (like 'ATL-G1'), its colour, its owner, "
            "its target date and a progress count taken from the cards linked "
            "to it: how many in all, how many are done, how many are left, and "
            "how many of those are blocked or on hold. Open goals come first, "
            "the nearest target date at the top. Use this before create_task or "
            "set_task_goal to see what a card could be written under."
        ),
    )
    async def list_goals(
        project: ProjectRef,
        open_only: Annotated[
            bool,
            Field(description="Leave out goals that have been achieved or dropped."),
        ] = False,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            goals = await every(
                client, f"/projects/{project}/goals", open_only=True if open_only else None
            )
            return {"goals": goals}

        return await as_tool_result(call)

    @server.tool(
        name="get_goal",
        description=(
            "One goal, its progress, and the cards linked to it in board "
            "order. The goal may be named ('Search revamp'), referenced "
            "('ATL-G1') or given as an id. Use this to answer what is left on "
            "an epic: the cards come back with their columns, owners and "
            "statuses, so you can say which are done and which are stuck "
            "without listing the whole board. The cards come a page at a time: "
            "when 'tasks_next' is not null there are more, and calling again "
            "with cards_after set to it returns the next page of them."
        ),
    )
    async def get_goal(
        project: ProjectRef,
        goal: GoalRef,
        cards_after: Annotated[
            str | None,
            Field(description="The 'tasks_next' or 'next' of an earlier call, for more cards."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            reference = await resolve.goal_ref(client, project, goal)
            if cards_after:
                page = await client.get(f"/goals/{reference}/tasks", after=cards_after)
                return {"tasks": page.get("items", []), "next": page.get("next")}
            return {"goal": await client.get(f"/goals/{reference}")}

        return await as_tool_result(call)

    @server.tool(
        name="create_goal",
        description=(
            "Start a goal on a project. Name and owner are required — the owner "
            "must already be a project member — and everything else is "
            "optional: leave the colour off and one is taken from the palette, "
            "and leave the target date off rather than inventing one. A goal is "
            "created empty; put cards on it afterwards with set_task_goal, or "
            "name it when you create one. Returns the new goal, including its "
            "reference."
        ),
    )
    async def create_goal(
        project: ProjectRef,
        name: Annotated[str, Field(description="What the goal is called, e.g. 'Search revamp'.")],
        owner: Annotated[
            str, Field(description="Who is answerable for it: a project member's name or id.")
        ],
        description: Annotated[
            str | None, Field(description="What reaching this goal means.")
        ] = None,
        target_date: Annotated[
            str | None,
            Field(description="ISO date, e.g. '2026-12-01'. Omit for a goal with no date."),
        ] = None,
        colour: Annotated[
            str | None,
            Field(description="Six-digit hex like '#3B6FC2'. Omit to take one from the palette."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            body: dict[str, Any] = {
                "name": name,
                "owner_id": await resolve.person_id(client, owner, project_ref=project),
            }
            if description:
                body["description"] = description
            if target_date:
                body["target_date"] = target_date
            if colour:
                body["colour"] = colour
            return {"goal": await client.post(f"/projects/{project}/goals", body)}

        return await as_tool_result(call)

    @server.tool(
        name="set_task_goal",
        description=(
            "Put a card on a goal, or take it off the one it is on by passing "
            "no goal at all. The goal may be named, referenced or given as an "
            "id, and must be on the same project as the card. A card belongs to "
            "at most one goal, so this replaces whatever it was on rather than "
            "adding to it. Refused on a sub-task: a sub-task belongs to its "
            "card, and its card is what belongs to a goal. Returns the task."
        ),
    )
    async def set_task_goal(
        task: TaskRef,
        goal: Annotated[
            str | None,
            Field(description="Goal name, reference or id. Omit to take the card off its goal."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            current = await client.get(f"/tasks/{task}")
            new_goal_id: str | None = None
            if goal:
                new_goal_id = await resolve.goal_id(client, project_of(current), goal)
            return {"task": await client.patch(f"/tasks/{task}", {"goal_id": new_goal_id})}

        return await as_tool_result(call)

    @server.tool(
        name="set_goal_status",
        description=(
            "Mark a goal 'achieved', 'dropped', or 'open' again. Achieving one "
            "is refused while a card on it is neither in the board's last "
            "column nor cancelled — the error names every card holding it open, "
            "so finish, cancel or unlink those first. Dropping a goal carries "
            "no such rule: giving up on one is exactly what you do while its "
            "work is unfinished, and its cards stay on the board either way. "
            "Returns the goal."
        ),
    )
    async def set_goal_status(
        project: ProjectRef,
        goal: GoalRef,
        status: Annotated[str, Field(description="One of 'open', 'achieved' or 'dropped'.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            check_choice("status", status, GOAL_STATUSES)
            reference = await resolve.goal_ref(client, project, goal)
            return {"goal": await client.patch(f"/goals/{reference}", {"status": status})}

        return await as_tool_result(call)
