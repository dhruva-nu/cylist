"""Tasks: the cards on a board, their sub-tasks, checklists, moves and comments."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import BaseModel, Field

from cylist_mcp import resolve
from cylist_mcp.client import Api
from cylist_mcp.errors import CylistError
from cylist_mcp.tools.common import (
    ProjectRef,
    TaskRef,
    as_tool_result,
    check_choice,
    check_status,
    project_of,
)

TASK_TYPES = ("feature", "bug", "chore")

CHECKLIST_STATES = ("open", "done", "cancelled")


class PullRequest(BaseModel):
    """One pull request on a card: where it is, and how far it got.

    A model rather than a bare string, because a reference carries a state as
    well as an address and the two belong together — a card says that its
    backend pull request merged and its frontend one is still waiting on a
    reviewer. Declared with the state's three values, so a model calling these
    tools is told what they are rather than finding out from a 422.
    """

    url: Annotated[
        str,
        Field(description="Where it is: a URL, or a short form such as '#212'."),
    ]
    state: Annotated[
        Literal["raised", "approved", "merged"],
        Field(
            description=(
                "How far it got: 'raised' while nobody has reviewed it, "
                "'approved' once somebody has said yes, 'merged' once it is in. "
                "Defaults to 'raised', which is where one starts."
            )
        ),
    ] = "raised"


PullRequests = Annotated[
    list[PullRequest] | None,
    Field(
        description=(
            "The pull requests this card's work lands as, each a URL or a "
            "short form such as '#212' plus how far it got. Pass several "
            "when the work took several — a backend pull request and the "
            "frontend one that calls it are still one card. Omit for a "
            "card with none yet."
        )
    ),
]
"""The ``pr_refs`` argument, shared by a card and a sub-task."""

DueDate = Annotated[
    str | None,
    Field(description="ISO date, e.g. '2026-03-31'. Omit for a card with no date."),
]

JiraRef = Annotated[str | None, Field(description="Jira issue key, if any.")]


def new_task_body(
    *,
    title: str,
    description: str,
    task_type: str,
    due_date: str | None,
    jira_ref: str | None,
    pr_refs: list[PullRequest] | None,
) -> dict[str, Any]:
    """The fields a card and a sub-task are created with alike.

    Optional fields are left out rather than sent empty, so the server's own
    defaults apply to whatever the model did not say.
    """
    check_choice("task_type", task_type, TASK_TYPES)
    body: dict[str, Any] = {"title": title, "description": description, "type": task_type}
    if due_date:
        body["due_date"] = due_date
    if jira_ref:
        body["jira_ref"] = jira_ref
    if pr_refs:
        body["pr_refs"] = [ref.model_dump() for ref in pr_refs]
    return body


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="list_tasks",
        description=(
            "List the cards on a project's board, in board order. Returns each "
            "task's reference (like 'ATL-41'), title, type, status, column, "
            "assignee, due date and who it is waiting on — not its description, "
            "which get_task reads — plus the board's columns so you can tell "
            "which card is where. 'agent_session' says who is working on a card "
            "right now, if an agent is: 'working', 'waiting' (it needs a human), "
            "or 'done' — which includes a session whose connection was lost, "
            "distinguished by its 'reason'.\n\n"
            "A board is read a page per column: the top 'limit' cards of each. "
            "Each column says its 'task_count' in all and carries 'next', a "
            "cursor for the cards below the ones returned — null when the column "
            "is all here. To read further down one column, call again with "
            "'column' and 'after' set to that cursor. The status and assignee "
            "filters narrow the cards this call returned, not the whole board, "
            "so a long column may hold matches further down."
        ),
    )
    async def list_tasks(
        project: ProjectRef,
        status: Annotated[
            str | None,
            Field(description="Only tasks in this status: 'active', 'hold' or 'blocked'."),
        ] = None,
        assignee: Annotated[
            str | None, Field(description="Only tasks assigned to this person, by name or id.")
        ] = None,
        column: Annotated[
            str | None,
            Field(description="Only this column, by name or id. Needed with 'after'."),
        ] = None,
        after: Annotated[
            str | None,
            Field(description="A column's 'next' from an earlier call: read the cards below it."),
        ] = None,
        limit: Annotated[
            int | None,
            Field(description="Cards per column, at most 100. Default 50.", ge=1, le=100),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            # Checked before anything is fetched: a bad status is the model's
            # mistake to fix, and two board-sized reads should not precede it.
            if status is not None:
                check_status(status)
            if after is not None and column is None:
                raise CylistError(
                    "'after' continues one column, so name it with 'column' as well.",
                    code="validation_failed",
                    details={"field": "column"},
                )
            board = await client.get(f"/projects/{project}/columns")
            columns: list[dict[str, Any]] = list(board.get("columns", []))

            tasks: list[dict[str, Any]]
            if column is not None:
                wanted_column = await resolve.column_id(client, project, column)
                page = await client.get(f"/columns/{wanted_column}/tasks", after=after, limit=limit)
                tasks = list(page.get("items", []))
                columns = [
                    {**entry, "next": page.get("next")}
                    for entry in columns
                    if str(entry.get("id")) == wanted_column
                ]
            else:
                stacks = await client.get(f"/projects/{project}/tasks", limit=limit)
                cursors = {
                    str(stack.get("column_id")): stack.get("next")
                    for stack in stacks.get("columns", [])
                }
                tasks = [
                    task for stack in stacks.get("columns", []) for task in stack.get("items", [])
                ]
                columns = [
                    {**entry, "next": cursors.get(str(entry.get("id")))} for entry in columns
                ]

            if status is not None:
                tasks = [task for task in tasks if task.get("status") == status]
            if assignee is not None:
                wanted = await resolve.person_id(client, assignee, project_ref=project)
                tasks = [
                    task for task in tasks if str((task.get("assignee") or {}).get("id")) == wanted
                ]
            return {"tasks": tasks, "columns": columns}

        return await as_tool_result(call)

    @server.tool(
        name="get_task",
        description=(
            "Get one task with its timeline. Returns the task's fields, its "
            "description, and its latest comments and status changes, oldest "
            "first — a status change carries the reason it was given and who it "
            "was waiting on. A long timeline is cut to its newest entries: "
            "'comment_count' says how many there are in all, and when "
            "'comments_next' is not null, calling again with comments_after set "
            "to it returns the entries before those, newest first, a page at a "
            "time. "
            "'agent_session' says whether an agent is on this card right now, and "
            "'agent_sessions' lists each harness session on it with what became "
            "of it — worth reading before you start, so two of you are not on "
            "the same card without knowing. What the work needs to know, ask "
            "the project's docs with ask_docs before reading the code."
        ),
    )
    async def get_task(
        task: TaskRef,
        comments_after: Annotated[
            str | None,
            Field(
                description="The 'comments_next' or 'next' of an earlier call, for the "
                "timeline entries before it."
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if comments_after:
                page = await client.get(f"/tasks/{task}/comments", after=comments_after)
                return {
                    "comments": page.get("items", []),
                    "next": page.get("next"),
                    "total": page.get("total"),
                }
            return {"task": await client.get(f"/tasks/{task}")}

        return await as_tool_result(call)

    @server.tool(
        name="read_task_history",
        description=(
            "Read what has been done to one task, newest first: every field "
            "edit with its old and new value, every move between columns, every "
            "sub-status step, every checklist item ticked — each with the moment "
            "it happened and who did it. Distinct from get_task's timeline, "
            "which is what people said about the card rather than what was done "
            "to it. 'channel' is 'web' if a person did it and 'api' if an agent "
            "did. A sub-task keeps its own history rather than appearing in its "
            "parent's. Comes a page at a time: the reply carries 'total' and "
            "'pages', so ask for the next page only if you still need it."
        ),
    )
    async def read_task_history(
        task: TaskRef,
        page: Annotated[int, Field(description="Which page, counting from 1.", ge=1)] = 1,
        per_page: Annotated[
            int, Field(description="Entries per page. Default 10, maximum 100.", ge=1, le=100)
        ] = 10,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            history = await client.get(f"/tasks/{task}/history", page=page, per_page=per_page)
            return {"history": history}

        return await as_tool_result(call)

    @server.tool(
        name="create_task",
        description=(
            "Add a task to a project's board. It always lands at the bottom of "
            "the board's first column; call move_task afterwards if it belongs "
            "elsewhere. Title, description and type are required by the server: "
            "a card with no description is the kind that goes stale. A due date "
            "is optional — leave it off rather than inventing one, because a "
            "card is only ever overdue against a date somebody actually chose. "
            "So is the assignee: leave it off and the card goes to whoever this "
            "token belongs to, which is whoever asked for it. Name somebody to "
            "put it on them instead — 'Agent' is the board's own machine — and "
            "whoever you name must already be a member of the project. `goal` "
            "puts the card under one of the project's epics — "
            "see list_goals — which is what colours it on the board. Returns "
            "the created task, including its new reference."
        ),
    )
    async def create_task(
        project: ProjectRef,
        title: Annotated[str, Field(description="One line naming the work.")],
        description: Annotated[str, Field(description="What done looks like.")],
        task_type: Annotated[str, Field(description="One of 'feature', 'bug' or 'chore'.")],
        assignee: Annotated[
            str | None,
            Field(
                description=(
                    "Who owns it: a project member's name or id. Omit it to put "
                    "the card on whoever this token belongs to."
                )
            ),
        ] = None,
        due_date: DueDate = None,
        goal: Annotated[
            str | None,
            Field(
                description=(
                    "The goal this card is work towards: a goal's name, "
                    "reference or id. Omit for a card that stands on its own."
                )
            ),
        ] = None,
        jira_ref: JiraRef = None,
        pr_refs: PullRequests = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            body = new_task_body(
                title=title,
                description=description,
                task_type=task_type,
                due_date=due_date,
                jira_ref=jira_ref,
                pr_refs=pr_refs,
            )
            if assignee:
                body["assignee_id"] = await resolve.person_id(client, assignee, project_ref=project)
            if goal:
                body["goal_id"] = await resolve.goal_id(client, project, goal)
            return {"task": await client.post(f"/projects/{project}/tasks", body)}

        return await as_tool_result(call)

    @server.tool(
        name="create_subtask",
        description=(
            "Split a task into a sub-task with a reference, an owner and a "
            "timeline of its own. A sub-task of ATL-41 is ATL-41-2, and is "
            "addressable by that everywhere a task reference is taken. It is not "
            "on the board: it has no column, it does not appear in list_tasks, "
            "and finish_subtask is what completes it rather than move_task. "
            "Sub-tasks go one level deep, so splitting a sub-task again is "
            "refused. Use add_checklist_item instead when the piece needs no "
            "owner and no reference of its own. The assignee is optional here "
            "too: leave it off and the piece stays with whoever this token "
            "belongs to. Returns the created sub-task."
        ),
    )
    async def create_subtask(
        task: Annotated[
            str, Field(description="The parent task: a reference such as 'ATL-41', or its id.")
        ],
        title: Annotated[str, Field(description="One line naming the work.")],
        description: Annotated[str, Field(description="What done looks like.")],
        task_type: Annotated[str, Field(description="One of 'feature', 'bug' or 'chore'.")],
        assignee: Annotated[
            str | None,
            Field(
                description=(
                    "Who owns it: a project member's name or id. Omit it to "
                    "keep the piece with whoever this token belongs to."
                )
            ),
        ] = None,
        due_date: DueDate = None,
        jira_ref: JiraRef = None,
        pr_refs: PullRequests = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            body = new_task_body(
                title=title,
                description=description,
                task_type=task_type,
                due_date=due_date,
                jira_ref=jira_ref,
                pr_refs=pr_refs,
            )
            if assignee:
                # Only an assignee needs the parent: it is resolved against the
                # parent's project, which the sub-task belongs to too.
                parent = await client.get(f"/tasks/{task}")
                body["assignee_id"] = await resolve.person_id(
                    client, assignee, project_ref=project_of(parent)
                )
            return {"task": await client.post(f"/tasks/{task}/subtasks", body)}

        return await as_tool_result(call)

    @server.tool(
        name="finish_subtask",
        description=(
            "Tick a sub-task off, or put it back. This is the only way a "
            "sub-task is finished: it is not on the board, so there is no last "
            "column to move it into. A finished sub-task stops holding its "
            "parent back, the same way a cancelled one does — cancel it with "
            "set_task_status when the work is not going to happen, and finish it "
            "here when it is done. Pass finished=false to reopen one. On a "
            "top-level card this is refused: a card is finished by move_task "
            "putting it in the board's last column. Returns the sub-task."
        ),
    )
    async def finish_subtask(
        task: Annotated[
            str, Field(description="The sub-task: a reference such as 'ATL-41-2', or its id.")
        ],
        finished: Annotated[
            bool, Field(description="True to tick it off, false to reopen it. Default true.")
        ] = True,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"task": await client.post(f"/tasks/{task}/finish", {"finished": finished})}

        return await as_tool_result(call)

    @server.tool(
        name="add_checklist_item",
        description=(
            "Add a tick-box sub-task to a task: a line of text with no card, no "
            "owner and no reference of its own. Use this for the 'and don't "
            "forget X' pieces — tell support, update the runbook — and "
            "create_subtask for anything that needs an owner and a due date. "
            "The item still has to be ticked or cancelled before the task can "
            "be moved into the board's last column. Returns the created item, "
            "whose id set_checklist_item takes."
        ),
    )
    async def add_checklist_item(
        task: TaskRef,
        title: Annotated[str, Field(description="What has to be done, in one line.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"item": await client.post(f"/tasks/{task}/checklist", {"title": title})}

        return await as_tool_result(call)

    @server.tool(
        name="set_checklist_item",
        description=(
            "Tick, cancel or reopen a tick-box sub-task, or retitle it. State is "
            "'done' for finished, 'cancelled' for work that is not going to "
            "happen, and 'open' to put it back. Done and cancelled both count as "
            "settled, so either one stops the item holding its task back. The "
            "item's id comes from get_task, which lists a task's checklist. "
            "Returns the updated item."
        ),
    )
    async def set_checklist_item(
        item: Annotated[str, Field(description="The checklist item's id, from get_task.")],
        state: Annotated[
            str | None, Field(description="One of 'open', 'done' or 'cancelled'.")
        ] = None,
        title: Annotated[str | None, Field(description="A new title for the item.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            body: dict[str, Any] = {}
            if state is not None:
                check_choice("state", state, CHECKLIST_STATES)
                body["state"] = state
            if title is not None:
                body["title"] = title
            if not body:
                raise CylistError(
                    "Nothing to change. Pass state, title, or both.",
                    code="validation_failed",
                    details={"field": "state"},
                )
            return {"item": await client.patch(f"/checklist/{item}", body)}

        return await as_tool_result(call)

    @server.tool(
        name="move_task",
        description=(
            "Move a card to another column on the same board. The column may be "
            "named ('In progress') or given as an id; get_project lists them. "
            "Position counts from the top of the column and is clamped to its "
            "length, so 0 puts the card first and a large number puts it last. "
            "Moving does not change the task's status, but moving a card into "
            "the board's last column finishes it and moving it back out reopens "
            "it — both are written to the card's history. Where that column is "
            "divided into outcomes — 'Done', 'Cancelled', 'In prod' — name one "
            "with `outcome` to say how the work ended; left unsaid, the card "
            "lands on the first its template allows. A card with a sub-task "
            "still open cannot be moved into the board's last column; that comes "
            "back as an error naming what is outstanding. A sub-task cannot be "
            "moved at all — it is not on the board; finish_subtask is what "
            "completes one. Returns the moved task."
        ),
    )
    async def move_task(
        task: TaskRef,
        column: Annotated[str, Field(description="Destination column name or id.")],
        position: Annotated[
            int, Field(description="Index from the top of the column. Default 0.", ge=0)
        ] = 0,
        outcome: Annotated[
            str | None,
            Field(
                description=(
                    "Which of the column's outcomes the card ends on, by name. Only the "
                    "board's last column has any; get_project lists them."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            current = await client.get(f"/tasks/{task}")
            column_id = await resolve.column_id(client, project_of(current), column)
            body: dict[str, Any] = {"column_id": column_id, "position": position}
            if outcome is not None:
                body["outcome"] = outcome
            return {"task": await client.post(f"/tasks/{task}/move", body)}

        return await as_tool_result(call)

    @server.tool(
        name="set_task_status",
        description=(
            "Move a task between 'active', 'hold' and 'blocked'. A reason is "
            "required for 'hold', 'blocked' and 'cancelled', and is written to the task's "
            "timeline, so say what is actually in the way rather than restating "
            "the status. waiting_on names the people the work now waits on — "
            "they must be project members — and is cleared when the task goes "
            "back to 'active'. Returns the updated task with its new timeline."
        ),
    )
    async def set_task_status(
        task: TaskRef,
        status: Annotated[
            str, Field(description="One of 'active', 'hold', 'blocked' or 'cancelled'.")
        ],
        reason: Annotated[
            str | None,
            Field(description="Why. Required for 'hold', 'blocked' and 'cancelled'."),
        ] = None,
        waiting_on: Annotated[
            list[str] | None,
            Field(description="Project members this now waits on, by name or id."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            check_status(status)
            if status != "active" and not (reason or "").strip():
                raise CylistError(
                    f"A reason is required to set a task to '{status}'. "
                    "Call again with reason set to what is blocking the work.",
                    code="unprocessable",
                    details={"field": "reason"},
                )

            current = await client.get(f"/tasks/{task}")
            project_ref = project_of(current)
            body: dict[str, Any] = {
                "status": status,
                "waiting_on": [
                    await resolve.person_id(client, name, project_ref=project_ref)
                    for name in waiting_on or []
                ],
            }
            if reason:
                body["reason"] = reason
            return {"task": await client.post(f"/tasks/{task}/status", body)}

        return await as_tool_result(call)

    @server.tool(
        name="add_comment",
        description=(
            "Add a comment to a task's timeline. Leave author unset to comment "
            "as the agent itself, which is usually right; set it only to record "
            "that a named project member said something. Returns the comment."
        ),
    )
    async def add_comment(
        task: TaskRef,
        body: Annotated[str, Field(description="The comment text.")],
        author: Annotated[
            str | None, Field(description="A project member's name or id. Omit for the agent.")
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            payload: dict[str, Any] = {"body": body}
            if author:
                current = await client.get(f"/tasks/{task}")
                payload["author_id"] = await resolve.person_id(
                    client, author, project_ref=project_of(current)
                )
            return {"comment": await client.post(f"/tasks/{task}/comments", payload)}

        return await as_tool_result(call)
