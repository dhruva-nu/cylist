"""The tools, and the scope check that decides which of them exist.

Two conventions run through every tool here.

**Errors are results, not exceptions.** A tool that raises gives the model an
opaque "error executing tool". Every tool below catches :class:`CylistError`
and returns a ``CallToolResult`` with ``is_error`` set, a sentence saying what
the server refused and why, and the API's own error envelope as structured
content. The model can then fix its arguments and try again, which is the whole
difference between a recoverable and an unrecoverable failure.

**A tool that cannot work is not offered.** ``reveal_secret`` is registered
only when ``GET /me`` says the configured token carries ``vault:reveal``, and
``add_secret`` / ``update_secret`` only when it carries ``write`` and
``vault:read``. An advertised tool that always returns 403 is worse than a
missing one: the model will call it, read the refusal, and reasonably try again
with different arguments, because from where it sits a 403 is
indistinguishable from a mistake it made.
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult, TextContent
from pydantic import Field

from cylist_mcp import __version__, resolve
from cylist_mcp.client import Api
from cylist_mcp.errors import CylistError

VAULT_REVEAL = "vault:reveal"

VAULT_WRITE = frozenset({"write", "vault:read"})
"""What the API asks of a vault write: ``write``, and ``vault:read`` to see
where in the tree it lands."""

SENSITIVITIES = ("public", "internal", "restricted")

SKILL_MAX_CHARS = 40_000
"""How much of a skill `read_skill` will return. A skill is a page of
instructions; anything past this is not one, and filling a context window
with it would be the wrong failure."""

DOC_MAX_CHARS = 40_000
"""How much of a doc `read_doc` will return, for the same reason as a skill."""

LEARNED = "learned.md"
"""The doc in each topic that agents add what they learn to, one line at a time."""

INSTRUCTIONS_SEEN = 2048
"""How much of the instructions a client can be relied on to show. Claude Code
cuts them off at this many characters, so what an agent must do on every card
goes before it; the tests hold the docs paragraph to that."""

INSTRUCTIONS = """\
Cylist is a project manager: each project has a Kanban board, a people
directory, files and a vault of credentials.

Every project has **docs**: markdown filed as section (Product,
Engineering) → topic → doc, much of it written by agents before you. Ask
them before you read the code, and teach them what they could not answer:

1. Whenever you would open the repo to answer a question — where a thing
   lives, how it works, why it is the way it is — `ask_docs` it first, in
   plain words. `ok` hands you the one section that answers it: act on it.
2. Any other status means the docs do not know yet. Find the answer in the
   code, then straight away — not at the end, a session can stop first —
   `place_doc` what you found, one fact per sentence, and make the edits it
   plans with `write_doc`, in the shape it says each doc is written in. The
   next agent to ask gets it from the docs.
3. Facts, not a progress log — the board already reports that. Topics are
   made by people, never agents. If a section has stopped being true, write
   the correction.

Two things are worth knowing before you start.

Projects and tasks are addressed by their human reference, not a UUID: a
project is `ATL`, a task on it is `ATL-41`. Anywhere a person, column or folder
is named you may pass the name you saw on screen ("In progress", "Aditi K")
and it will be resolved; an ambiguous name comes back as an error listing the
candidates rather than a guess.

A task cannot be put on hold or blocked without a reason. That is enforced by
the server, not by politeness — `set_task_status` needs `reason` for `hold`
and `blocked`, and `waiting_on` should name whoever the work is now waiting on.
The same goes for `cancelled`, which is how work that is not going to happen
stops holding up the card it belongs to.

A card can be written under a **goal** — an epic like "Search revamp" — which
is what the board colours it by. `list_goals` shows a project's goals with the
progress counted from their own cards; `set_task_goal` puts a card on one or
takes it off. A card belongs to at most one goal, and a goal cannot be marked
achieved while a card on it is still open — `set_goal_status` refuses that and
names the cards holding it open.

A task can be split two ways. `create_subtask` makes a sub-task with a
reference of its own, `ATL-41-2`, addressable like any other task and carrying
its own owner, due date and timeline; `add_checklist_item` makes a tick box that
lives on the parent alone. Neither is on the board — a sub-task has no column,
so `move_task` refuses one and `finish_subtask` is what completes it. Either
way, every one of them has to be finished or cancelled before the parent can be
moved into the board's last column, and `move_task` refuses that too, naming
what is still outstanding.

Each project carries what its agents work from. `list_skills` and `read_skill`
give you the procedures somebody has already written down for this project —
worth a look before improvising one.

Progress is reported for you. When a session is bound to a card — someone ran
`cylist work ATL-41`, or typed `/work ATL-41` — the harness's own hooks tell
the board when you are working, when you are waiting on a human, and when the
session ends. You do not need to announce that you have started or finished,
and there is no tool for it; `agent_session` on a card is what that reporting
looks like from the outside.

The board's last column is where a card is done: moving one in records the
moment it was finished, and moving it back out reopens it. That column alone
may be divided into up to three **outcomes** — "Done", "Cancelled", "In prod" —
which `get_project` lists beside it; `move_task` takes the name of one to say
how the work ended. A template may narrow which of them its own cards are
allowed to end on.
"""


def build_server(client: Api, scopes: frozenset[str], *, hosted: bool = False) -> MCPServer:
    """Wire the tools onto one API client, honouring the token's scopes.

    ``hosted`` is for the copy served over HTTP by the backend itself (see
    :mod:`cylist_mcp.hosted`). The tools are the same; what changes is that
    "this machine" is no longer the caller's, so nothing is defaulted from it.
    """
    # The day a report is cut by. Beside the caller, this machine's zone is the
    # right guess; on the server it is the container's, which is an accident of
    # how the image was built, so the API's own UTC is the more honest default.
    default_zone: Callable[[], str | None] = (lambda: None) if hosted else local_timezone
    zone_default_text = (
        "defaults to UTC, because this server does not run beside you — pass the user's own zone"
        if hosted
        else "defaults to this machine's own zone rather than UTC"
    )
    server = MCPServer(
        name="cylist",
        version=__version__,
        instructions=INSTRUCTIONS,
    )

    # --- Projects ----------------------------------------------------------

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

        return await _as_tool_result(call)

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
    async def get_project(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            summary = await client.get(f"/projects/{project}/summary")
            board = await client.get(f"/projects/{project}/columns")
            return {"project": summary, "columns": board.get("columns", [])}

        return await _as_tool_result(call)

    # --- Tasks -------------------------------------------------------------

    @server.tool(
        name="list_tasks",
        description=(
            "List the cards on a project's board, in board order. Returns each "
            "task's reference (like 'ATL-41'), title, type, status, column, "
            "assignee, due date and who it is waiting on, plus the board's "
            "columns so you can tell which card is where. 'agent_session' says "
            "who is working on a card right now, if an agent is: 'working', "
            "'waiting' (it needs a human), or 'done' — which includes a session "
            "whose connection was lost, distinguished by its 'reason'. "
            "Optionally filter by status or assignee."
        ),
    )
    async def list_tasks(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        status: Annotated[
            str | None,
            Field(description="Only tasks in this status: 'active', 'hold' or 'blocked'."),
        ] = None,
        assignee: Annotated[
            str | None, Field(description="Only tasks assigned to this person, by name or id.")
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            tasks = await client.get(f"/projects/{project}/tasks")
            board = await client.get(f"/projects/{project}/columns")

            if status is not None:
                _check_status(status)
                tasks = [task for task in tasks if task.get("status") == status]
            if assignee is not None:
                wanted = await resolve.person_id(client, assignee, project_ref=project)
                tasks = [
                    task for task in tasks if str((task.get("assignee") or {}).get("id")) == wanted
                ]
            return {"tasks": tasks, "columns": board.get("columns", [])}

        return await _as_tool_result(call)

    @server.tool(
        name="get_task",
        description=(
            "Get one task with its whole timeline. Returns the task's fields and "
            "every comment and status change on it, oldest first — a status "
            "change carries the reason it was given and who it was waiting on. "
            "'agent_session' says whether an agent is on this card right now, and "
            "'agent_sessions' lists each harness session on it with what became "
            "of it — worth reading before you start, so two of you are not on "
            "the same card without knowing. What the work needs to know, ask "
            "the project's docs with ask_docs before reading the code."
        ),
    )
    async def get_task(
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"task": await client.get(f"/tasks/{task}")}

        return await _as_tool_result(call)

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
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
        page: Annotated[int, Field(description="Which page, counting from 1.", ge=1)] = 1,
        per_page: Annotated[
            int, Field(description="Entries per page. Default 10, maximum 100.", ge=1, le=100)
        ] = 10,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            history = await client.get(f"/tasks/{task}/history", page=page, per_page=per_page)
            return {"history": history}

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
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
        due_date: Annotated[
            str | None,
            Field(description="ISO date, e.g. '2026-03-31'. Omit for a card with no date."),
        ] = None,
        goal: Annotated[
            str | None,
            Field(
                description=(
                    "The goal this card is work towards: a goal's name, "
                    "reference or id. Omit for a card that stands on its own."
                )
            ),
        ] = None,
        jira_ref: Annotated[str | None, Field(description="Jira issue key, if any.")] = None,
        pr_refs: Annotated[
            list[str] | None,
            Field(
                description=(
                    "The pull requests this card's work lands as, each a URL or a "
                    "short form such as '#212'. Pass several when the work took "
                    "several — a backend pull request and the frontend one that "
                    "calls it are still one card. Omit for a card with none yet."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("task_type", task_type, ("feature", "bug", "chore"))
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "type": task_type,
            }
            if assignee:
                body["assignee_id"] = await resolve.person_id(client, assignee, project_ref=project)
            if goal:
                reference = await resolve.goal_ref(client, project, goal)
                body["goal_id"] = (await client.get(f"/goals/{reference}"))["id"]
            if due_date:
                body["due_date"] = due_date
            if jira_ref:
                body["jira_ref"] = jira_ref
            if pr_refs:
                body["pr_refs"] = pr_refs
            return {"task": await client.post(f"/projects/{project}/tasks", body)}

        return await _as_tool_result(call)

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
        due_date: Annotated[
            str | None,
            Field(description="ISO date, e.g. '2026-03-31'. Omit for a card with no date."),
        ] = None,
        jira_ref: Annotated[str | None, Field(description="Jira issue key, if any.")] = None,
        pr_refs: Annotated[
            list[str] | None,
            Field(
                description=(
                    "The pull requests this card's work lands as, each a URL or a "
                    "short form such as '#212'. Pass several when the work took "
                    "several — a backend pull request and the frontend one that "
                    "calls it are still one card. Omit for a card with none yet."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("task_type", task_type, ("feature", "bug", "chore"))
            parent = await client.get(f"/tasks/{task}")
            project_ref = _project_of(parent)
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "type": task_type,
            }
            if assignee:
                body["assignee_id"] = await resolve.person_id(
                    client, assignee, project_ref=project_ref
                )
            if due_date:
                body["due_date"] = due_date
            if jira_ref:
                body["jira_ref"] = jira_ref
            if pr_refs:
                body["pr_refs"] = pr_refs
            return {"task": await client.post(f"/tasks/{task}/subtasks", body)}

        return await _as_tool_result(call)

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

        return await _as_tool_result(call)

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
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
        title: Annotated[str, Field(description="What has to be done, in one line.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"item": await client.post(f"/tasks/{task}/checklist", {"title": title})}

        return await _as_tool_result(call)

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
                _check_choice("state", state, ("open", "done", "cancelled"))
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

        return await _as_tool_result(call)

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
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
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
            column_id = await resolve.column_id(client, _project_of(current), column)
            body: dict[str, Any] = {"column_id": column_id, "position": position}
            if outcome is not None:
                body["outcome"] = outcome
            moved = await client.post(f"/tasks/{task}/move", body)
            return {"task": moved}

        return await _as_tool_result(call)

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
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
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
            _check_status(status)
            if status != "active" and not (reason or "").strip():
                raise CylistError(
                    f"A reason is required to set a task to '{status}'. "
                    "Call again with reason set to what is blocking the work.",
                    code="unprocessable",
                    details={"field": "reason"},
                )

            current = await client.get(f"/tasks/{task}")
            project_ref = _project_of(current)
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

        return await _as_tool_result(call)

    @server.tool(
        name="add_comment",
        description=(
            "Add a comment to a task's timeline. Leave author unset to comment "
            "as the agent itself, which is usually right; set it only to record "
            "that a named project member said something. Returns the comment."
        ),
    )
    async def add_comment(
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
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
                    client, author, project_ref=_project_of(current)
                )
            return {"comment": await client.post(f"/tasks/{task}/comments", payload)}

        return await _as_tool_result(call)

    # --- Goals -------------------------------------------------------------

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        open_only: Annotated[
            bool,
            Field(description="Leave out goals that have been achieved or dropped."),
        ] = False,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            goals = await client.get(
                f"/projects/{project}/goals", open_only=True if open_only else None
            )
            return {"goals": goals}

        return await _as_tool_result(call)

    @server.tool(
        name="get_goal",
        description=(
            "One goal, its progress, and every card linked to it in board "
            "order. The goal may be named ('Search revamp'), referenced "
            "('ATL-G1') or given as an id. Use this to answer what is left on "
            "an epic: the cards come back with their columns, owners and "
            "statuses, so you can say which are done and which are stuck "
            "without listing the whole board."
        ),
    )
    async def get_goal(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        goal: Annotated[str, Field(description="Goal name, reference such as 'ATL-G1', or id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            reference = await resolve.goal_ref(client, project, goal)
            return {"goal": await client.get(f"/goals/{reference}")}

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
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

        return await _as_tool_result(call)

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
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
        goal: Annotated[
            str | None,
            Field(description="Goal name, reference or id. Omit to take the card off its goal."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            current = await client.get(f"/tasks/{task}")
            goal_id: str | None = None
            if goal:
                reference = await resolve.goal_ref(client, _project_of(current), goal)
                goal_id = str((await client.get(f"/goals/{reference}"))["id"])
            return {"task": await client.patch(f"/tasks/{task}", {"goal_id": goal_id})}

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        goal: Annotated[str, Field(description="Goal name, reference such as 'ATL-G1', or id.")],
        status: Annotated[str, Field(description="One of 'open', 'achieved' or 'dropped'.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("status", status, ("open", "achieved", "dropped"))
            reference = await resolve.goal_ref(client, project, goal)
            return {"goal": await client.patch(f"/goals/{reference}", {"status": status})}

        return await _as_tool_result(call)

    # --- People ------------------------------------------------------------

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
                _check_choice("kind", kind, ("team", "client"))
            if project:
                people = (await client.get(f"/projects/{project}/members")).get("members", [])
                if kind:
                    people = [person for person in people if person.get("kind") == kind]
            else:
                people = await client.get("/people", kind=kind)
            return {"people": people}

        return await _as_tool_result(call)

    # --- Files -------------------------------------------------------------

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
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
            return {
                "folder": listing.get("folder"),
                "folders": listing.get("folders", []),
                "items": listing.get("items", []),
            }

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
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
            _check_choice("source", source, ("sharepoint", "gdrive", "other"))
            folder_id = await resolve.folder_id(client, project, folder)
            body: dict[str, Any] = {"name": name, "url": url, "source": source}
            if added_by:
                body["added_by"] = await resolve.person_id(client, added_by, project_ref=project)
            return {"item": await client.post(f"/folders/{folder_id}/links", body)}

        return await _as_tool_result(call)

    # --- Vault -------------------------------------------------------------

    @server.tool(
        name="list_vault",
        description=(
            "List a project's vault. With no tree, returns the trees with how "
            "many nodes and secrets each holds. With a tree name, returns its "
            "whole structure: branches, secrets, and each secret's username, "
            "URL and notes. No response from this tool ever contains a "
            "credential — reading one is a separate, scoped, audited action."
        ),
    )
    async def list_vault(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        tree: Annotated[str | None, Field(description="A tree's name, e.g. 'Logins'.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            trees = await client.get(f"/projects/{project}/vault/trees")
            if not tree:
                return {"trees": trees}
            summary = resolve.pick(list(trees), tree, kind="vault tree", where=project)
            return {"tree": await client.get(f"/vault/trees/{summary['id']}")}

        return await _as_tool_result(call)

    # --- Audit -------------------------------------------------------------

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

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
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

        return await _as_tool_result(call)

    # --- Skills ------------------------------------------------------------

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
    async def list_skills(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"skills": await client.get(f"/projects/{project}/skills")}

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        name: Annotated[str, Field(description="The skill's name, e.g. 'board-tidy.md'.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            skill = await _find_skill(client, project, name)
            if not _is_zip(skill):
                text, truncated = await client.get_text(
                    f"/skills/{skill['id']}/download", max_chars=SKILL_MAX_CHARS
                )
                return {"skill": skill, "content": text, "truncated": truncated}

            # The bytes of a zip are no use to a model. What it wants is the
            # instructions, which are SKILL.md once the server has unpacked it.
            folder = await client.get(f"/skills/{skill['id']}/folder")
            files = folder.get("files", [])
            text = _skill_md(files)
            return {
                "skill": skill,
                "content": text[:SKILL_MAX_CHARS],
                "truncated": len(text) > SKILL_MAX_CHARS,
                "files": [entry.get("path") for entry in files],
            }

        return await _as_tool_result(call)

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
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        name: Annotated[
            str,
            Field(description="The skill's name as list_skills gave it, e.g. 'cylist.zip'."),
        ],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            skill = await _find_skill(client, project, name)
            folder = await client.get(f"/skills/{skill['id']}/folder")
            directory = f".claude/skills/{folder.get('folder')}/"
            return {
                **folder,
                "install": {
                    "command": f"cylist skills pull {project} {skill['name']}",
                    "directory": directory,
                },
            }

        return await _as_tool_result(call)

    # --- Docs --------------------------------------------------------------

    @server.tool(
        name="list_docs",
        description=(
            "List a project's docs as the tree they are filed in: both "
            "sections, Product then Engineering, each with its topics in order "
            "and each topic with its docs in order — titles, ids and authors, "
            "not bodies; read_doc fetches one. To find something out, "
            "ask_docs is the way in; this is for looking around."
        ),
    )
    async def list_docs(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"docs": await client.get(f"/projects/{project}/docs")}

        return await _as_tool_result(call)

    @server.tool(
        name="read_doc",
        description=(
            "Read one doc's markdown, with the section and topic it is filed "
            "under. Name it by the id ask_docs or list_docs gave, or by its "
            "path with the project: 'Engineering / MCP / learned.md', or "
            "'MCP / learned.md', or just the title when only one doc has it. "
            f"Truncated past {DOC_MAX_CHARS} characters, which is said in the "
            "result when it happens."
        ),
    )
    async def read_doc(
        doc: Annotated[
            str,
            Field(description="The doc's id, or its path: 'Engineering / MCP / learned.md'."),
        ],
        project: Annotated[
            str | None,
            Field(description="Project key such as 'ATL'. Needed when `doc` is a path."),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            doc_id = await resolve.doc_id(client, project, doc)
            found = dict(await client.get(f"/docs/{doc_id}"))
            body = str(found.get("body", ""))
            found["body"] = body[:DOC_MAX_CHARS]
            return {"doc": found, "truncated": len(body) > DOC_MAX_CHARS}

        return await _as_tool_result(call)

    @server.tool(
        name="ask_docs",
        description=(
            "Ask a project's docs a question — before you open the code to "
            "answer it. jev-docs routes it to the one section that answers it, "
            "then reads that section against the question. 'status' says what "
            "you got:\n"
            "- ok: 'found' is the section, with its text. Act on it.\n"
            "- ambiguous: 'found' passed the check but the route was close; "
            "glance at 'alternatives'.\n"
            "- unverified: nothing passed the check; 'found' is only the best "
            "guess. not_documented: nothing is written on it. unavailable: "
            "jev could not be asked, 'reason' says why.\n"
            "For those last three, find the answer in the code, then "
            "place_doc it and write it with write_doc, so the next agent to "
            "ask is answered."
        ),
    )
    async def ask_docs(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        question: Annotated[
            str,
            Field(
                description=(
                    "What you want to know, as you would ask a colleague: "
                    "'where is the check that stops a sub-task being moved?'"
                )
            ),
        ],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return dict(await client.post(f"/projects/{project}/docs/ask", {"question": question}))

        return await _as_tool_result(call)

    @server.tool(
        name="place_doc",
        description=(
            "Plan where what you found in the code goes in a project's docs, "
            "after ask_docs had no answer for it. Nothing is written: the "
            "result is 'edits', in the order to make them, each with the doc "
            "('doc_id', 'path') or, for a new doc, the topic ('topic_id') it "
            "belongs under. Make the 'sure' ones with write_doc; the others "
            "are worth a look. Match each doc's 'doc_shape':\n"
            "- indexed: add an '## Index' entry 'N. Title — blurb' and a "
            "matching '## Title' section (read_doc it and send the whole "
            "body back with `doc`).\n"
            "- headings: add a '## Title' section — write_doc with `doc` and "
            "append.\n"
            f"- bullets: one '- ' line, appended, as on a {LEARNED}.\n"
            "- plain: anywhere.\n"
            "A new doc is best written the indexed way: '# Title', two to five "
            "lines saying what it covers, '## Index', then its sections. "
            "'new_topic' means no topic fits; topics are made by people, so "
            "file it under the nearest and say so on the card. 'unavailable' "
            "means jev could not be asked: choose the topic from list_docs."
        ),
    )
    async def place_doc(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        title: Annotated[
            str, Field(description="A short name for what you found: 'Refund webhooks'.")
        ],
        facts: Annotated[
            list[str],
            Field(
                description=(
                    "What you found, one fact per item, each a sentence the next "
                    "reader could act on without the code open."
                ),
                min_length=1,
            ),
        ],
        summary: Annotated[
            str | None, Field(description="One line on what the facts are about.")
        ] = None,
        entities: Annotated[
            list[str] | None,
            Field(
                description=("Names a reader would type to find this: 'move_task', 'stripe_event'.")
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            request: dict[str, Any] = {
                "title": title,
                "facts": [{"text": fact} for fact in facts],
            }
            if summary:
                request["summary"] = summary
            if entities:
                request["entities"] = entities
            return dict(await client.post(f"/projects/{project}/docs/place", request))

        return await _as_tool_result(call)

    @server.tool(
        name="write_doc",
        description=(
            "Write markdown into a project's docs — the edits place_doc "
            "planned, made the moment you have the answer, not at the end of "
            "the session. Three ways:\n"
            "- An edit: `doc` names one already there, by id or path; the body "
            "you send replaces its body, or with append is added to the end.\n"
            "- A new doc: a title, a body and `topic`, the topic place_doc "
            "named ('Engineering / MCP'). It may be left out only on a project "
            "with one topic; otherwise nothing is written and the result lists "
            "the topics. You cannot create topics; people do.\n"
            f"- A line on a topic's {LEARNED}: title '{LEARNED}', `topic`, "
            "append true and one '- ' line as the body; the doc is made if the "
            "topic has none.\n"
            "A title the topic already holds is refused unless append is set, "
            "so an existing doc is never overwritten by accident."
        ),
    )
    async def write_doc(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        body: Annotated[str, Field(description="Markdown.")],
        title: Annotated[
            str | None,
            Field(
                description=(
                    f"The doc's title — '{LEARNED}' for something learned. "
                    "Needed unless `doc` names one to edit; with `doc`, a new "
                    "title renames it."
                )
            ),
        ] = None,
        topic: Annotated[
            str | None,
            Field(
                description=(
                    "Where to file a new doc: 'Engineering / MCP', or a topic's "
                    "name or id — the topic place_doc named."
                )
            ),
        ] = None,
        doc: Annotated[
            str | None,
            Field(
                description=(
                    "An existing doc to edit, by id or path "
                    "('Engineering / MCP / learned.md'). Leave it out to write "
                    "by title."
                )
            ),
        ] = None,
        append: Annotated[
            bool,
            Field(
                description=(
                    "Add `body` to the end of the doc rather than replacing or "
                    f"refusing. Always true for a line on {LEARNED}."
                )
            ),
        ] = False,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if doc is not None:
                return await _edit_doc(client, project, doc, title=title, body=body, append=append)
            if not title:
                raise CylistError(
                    "Give the doc a title, or name the doc to edit with `doc`.",
                    code="validation_failed",
                )
            request: dict[str, Any] = {"title": title, "body": body, "append": append}
            if topic is not None:
                request["topic_id"] = await resolve.doc_topic_id(client, project, topic)
            try:
                return dict(await client.post(f"/projects/{project}/docs", request))
            except CylistError as exc:
                raise _with_topics_listed(exc) from exc

        return await _as_tool_result(call)

    if scopes >= VAULT_WRITE:
        _register_vault_writes(server, client)
    if VAULT_REVEAL in scopes:
        _register_reveal(server, client)

    return server


def _register_vault_writes(server: MCPServer, client: Api) -> None:
    """Add ``add_secret`` and ``update_secret``. Only with ``write`` and ``vault:read``.

    Both name a secret by its path and match every part of it whole (see
    :func:`resolve.named_exactly`): these write, and a near miss here is a
    credential filed in the wrong place or overwritten. Neither response
    carries the value — the API never returns one outside ``reveal``.
    """

    @server.tool(
        name="add_secret",
        description=(
            "Store a new credential in a project's vault, at a path given as "
            "tree/branch/name, e.g. 'Logins/Staging/Admin'. The tree must already "
            "exist — list_vault shows them; people make trees in the Vault tab. "
            "Branches along the path that do not exist yet are created, and the "
            "result names them. A secret already at that path is refused; "
            "update_secret changes one. The value is encrypted before it is "
            "stored, is never in the response, and must not be repeated in a "
            "comment or a summary."
        ),
    )
    async def add_secret(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        path: Annotated[
            str,
            Field(description="Where it goes: tree/branch/name, e.g. 'Logins/Staging/Admin'."),
        ],
        value: Annotated[
            str, Field(description="The credential itself: a password, key or token.")
        ],
        username: Annotated[
            str | None, Field(description="The login it goes with, if any.")
        ] = None,
        url: Annotated[str | None, Field(description="Where it is used, if anywhere.")] = None,
        notes: Annotated[
            str, Field(description="Anything the next person needs to use it. Not secret.")
        ] = "",
        sensitivity: Annotated[
            str | None,
            Field(
                description=(
                    "'public', 'internal' or 'restricted'. Omit to inherit the "
                    "branch's, or the tree's default at the top level."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if not value:
                raise CylistError("A secret needs a value.", code="validation_failed")
            if sensitivity is not None:
                _check_choice("sensitivity", sensitivity, SENSITIVITIES)
            segments = resolve.vault_segments(path)
            tree = await resolve.vault_tree_exactly(client, project, segments[0])

            # Walk to the parent, making the branches that are missing. Every
            # check that can fail without a write has already run above.
            level: list[dict[str, Any]] = list(tree.get("nodes", []))
            walked = [str(tree["name"])]
            parent_id: str | None = None
            created: list[str] = []
            for segment in segments[1:-1]:
                branch = resolve.named_exactly(level, segment)
                if branch is None:
                    branch = await client.post(
                        "/vault/nodes",
                        {
                            "tree_id": tree["id"],
                            "parent_id": parent_id,
                            "name": segment,
                            "kind": "branch",
                        },
                    )
                    created.append("/".join([*walked, str(branch["name"])]))
                elif branch.get("kind") != "branch":
                    raise CylistError(
                        f"{'/'.join([*walked, str(branch['name'])])!r} is a secret, "
                        "so nothing can be filed under it.",
                        code="unprocessable",
                    )
                walked.append(str(branch["name"]))
                parent_id = str(branch["id"])
                children = branch.get("children", [])
                level = children if isinstance(children, list) else []

            name = segments[-1]
            taken = resolve.named_exactly(level, name)
            if taken is not None:
                where = "/".join([*walked, str(taken["name"])])
                raise CylistError(
                    f"{where!r} already exists, as a {taken.get('kind', 'node')}. "
                    "update_secret changes a secret; pick another name for a new one.",
                    code="conflict",
                )

            secret: dict[str, Any] = {"value": value, "notes": notes}
            if username is not None:
                secret["username"] = username
            if url is not None:
                secret["url"] = url
            body: dict[str, Any] = {
                "tree_id": tree["id"],
                "parent_id": parent_id,
                "name": name,
                "kind": "secret",
                "secret": secret,
            }
            if sensitivity is not None:
                body["sensitivity"] = sensitivity
            node = await client.post("/vault/nodes", body)
            return {
                "secret": node,
                "path": "/".join([*walked, str(node["name"])]),
                "created_branches": created,
            }

        return await _as_tool_result(call)

    @server.tool(
        name="update_secret",
        description=(
            "Change a credential already in a project's vault, named by its path "
            "as tree/branch/name, e.g. 'Logins/Staging/Admin'. Give only what "
            "changes: a new value (a rotated password or key), username, URL, "
            "notes, a new name, or sensitivity. Anything left out stays as it "
            "is, so a note or a username can be corrected without the value. "
            "The value is never in the response, and must not be repeated in a "
            "comment or a summary."
        ),
    )
    async def update_secret(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        path: Annotated[str, Field(description="The secret's path, e.g. 'Logins/Staging/Admin'.")],
        value: Annotated[
            str | None, Field(description="A new credential, replacing the stored one.")
        ] = None,
        username: Annotated[str | None, Field(description="A new login to go with it.")] = None,
        url: Annotated[str | None, Field(description="A new address it is used at.")] = None,
        notes: Annotated[
            str | None, Field(description="New notes, replacing the old ones. Not secret.")
        ] = None,
        name: Annotated[
            str | None, Field(description="Rename it; it stays on the same branch.")
        ] = None,
        sensitivity: Annotated[
            str | None, Field(description="Reclassify it: 'public', 'internal' or 'restricted'.")
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            if value == "":
                raise CylistError(
                    "A secret's value cannot be emptied; leave value out to keep it.",
                    code="validation_failed",
                )
            if sensitivity is not None:
                _check_choice("sensitivity", sensitivity, SENSITIVITIES)
            changes = {"value": value, "username": username, "url": url, "notes": notes}
            secret = {field: given for field, given in changes.items() if given is not None}
            body: dict[str, Any] = {}
            if secret:
                body["secret"] = secret
            if name is not None:
                body["name"] = name
            if sensitivity is not None:
                body["sensitivity"] = sensitivity
            if not body:
                raise CylistError(
                    "Nothing to change: give a value, username, url, notes, name or sensitivity.",
                    code="validation_failed",
                )

            _, node, where = await resolve.vault_node_exactly(client, project, path)
            if node.get("kind") != "secret":
                raise CylistError(
                    f"{where!r} is a branch, not a secret, so it holds nothing to update.",
                    code="unprocessable",
                )
            updated = await client.patch(f"/vault/nodes/{node['id']}", body)
            return {"secret": updated, "changed": sorted({*secret, *body} - {"secret"})}

        return await _as_tool_result(call)


def _register_reveal(server: MCPServer, client: Api) -> None:
    """Add ``reveal_secret``. Only called when the token carries the scope."""

    @server.tool(
        name="reveal_secret",
        description=(
            "Decrypt and return one stored credential. This is the only way to "
            "read a secret's value, it requires the vault:reveal scope, and "
            "every call is written to the audit feed naming the secret and the "
            "token that asked. Treat the value as sensitive: use it, do not "
            "repeat it back in a summary, and do not write it into a task "
            "comment or a file. Give the path as tree/branch/name, e.g. "
            "'Logins/Billing/Stripe'."
        ),
    )
    async def reveal_secret(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        path: Annotated[
            str, Field(description="Path within the vault, e.g. 'Logins/Billing/Stripe'.")
        ],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            tree, node = await resolve.vault_node(client, project, path)
            if node.get("kind") != "secret":
                raise CylistError(
                    f"{node['name']!r} is a branch in {tree['name']!r}, not a secret, "
                    "so it holds no value to reveal.",
                    code="unprocessable",
                )
            return {"secret": await client.post(f"/vault/nodes/{node['id']}/reveal")}

        return await _as_tool_result(call)


# --- Plumbing --------------------------------------------------------------


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


async def _as_tool_result(call: Callable[[], Awaitable[dict[str, Any]]]) -> CallToolResult:
    """Run a tool body, turning a failure into a result the model can read."""
    try:
        payload = await call()
    except CylistError as exc:
        return CallToolResult(
            content=[TextContent(type="text", text=exc.message)],
            structured_content=exc.envelope(),
            is_error=True,
        )
    return CallToolResult(
        content=[TextContent(type="text", text=json.dumps(payload, indent=2, default=str))],
        structured_content=payload,
        is_error=False,
    )


async def _find_skill(client: Api, project: str, name: str) -> dict[str, Any]:
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


async def _edit_doc(
    client: Api, project: str, ref: str, *, title: str | None, body: str, append: bool
) -> dict[str, Any]:
    """Replace or add to the body of a doc that is already there."""
    doc_id = await resolve.doc_id(client, project, ref)
    if not append:
        change: dict[str, Any] = {"body": body}
        if title:
            change["title"] = title
        return {"doc": await client.patch(f"/docs/{doc_id}", change), "created": False}

    # Appending goes through the write route, which joins the two bodies on the
    # server — so a line added by another agent a moment ago is not lost.
    current = await client.get(f"/docs/{doc_id}")
    written = await client.post(
        f"/projects/{current['project_id']}/docs",
        {"title": current["title"], "topic_id": current["topic_id"], "body": body, "append": True},
    )
    return dict(written)


def _with_topics_listed(exc: CylistError) -> CylistError:
    """A topic refusal with the topics written into its message.

    The server lists them in the error's details, but the message is what a
    model reads first, and a refusal that says "name a topic" without saying
    which ones there are costs it a list_docs to recover from.
    """
    topics = exc.details.get("topics")
    if exc.code != "topic_unclear" or not isinstance(topics, list) or not topics:
        return exc

    listed = "; ".join(f"{topic.get('section')} / {topic.get('name')}" for topic in topics)
    return CylistError(
        f"{exc.message} Topics: {listed}. Call write_doc again with `topic` set to one.",
        code=exc.code,
        status_code=exc.status_code,
        details=exc.details,
    )


def _is_zip(skill: dict[str, Any]) -> bool:
    named_as_zip = str(skill.get("name", "")).lower().endswith(".zip")
    typed_as_zip = "zip" in str(skill.get("mime", ""))
    return named_as_zip or typed_as_zip


def _skill_md(files: list[dict[str, Any]]) -> str:
    """The text of the SKILL.md among an unpacked skill's files, or ``""``."""
    for entry in files:
        if entry.get("path") == "SKILL.md":
            return str(entry["content"])
    return ""


def _project_of(task: dict[str, Any]) -> str:
    """The project key behind a task, from its reference: 'ATL-41' -> 'ATL'.

    Used in place of ``project_id`` wherever the value may reach the model in
    a message: "no person called 'Le' in ATL's members" is actionable, the
    same sentence with a UUID in it is not.

    Split from the front rather than the back, because a sub-task's reference
    carries two numbers — ``ATL-41-2`` — and only the key is wanted. Keys
    cannot contain a dash, so the first segment is always it.
    """
    key = str(task.get("reference", "")).split("-", 1)[0]
    return key or str(task["project_id"])


def _check_status(status: str) -> None:
    _check_choice("status", status, ("active", "hold", "blocked", "cancelled"))


def _check_choice(field: str, value: str, allowed: tuple[str, ...]) -> None:
    """Reject a bad enum locally, naming the alternatives.

    The server would reject it too, but its validation error is a list of
    Pydantic field errors. A sentence naming the three options is something a
    model can act on in one turn.
    """
    if value not in allowed:
        raise CylistError(
            f"{field} must be one of {', '.join(repr(item) for item in allowed)}, not {value!r}.",
            code="validation_failed",
            details={"field": field, "allowed": list(allowed)},
        )
