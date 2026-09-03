"""The tools, and the scope check that decides which of them exist.

Two conventions run through every tool here.

**Errors are results, not exceptions.** A tool that raises gives the model an
opaque "error executing tool". Every tool below catches :class:`CylistError`
and returns a ``CallToolResult`` with ``is_error`` set, a sentence saying what
the server refused and why, and the API's own error envelope as structured
content. The model can then fix its arguments and try again, which is the whole
difference between a recoverable and an unrecoverable failure.

**A tool that cannot work is not offered.** ``reveal_secret`` is registered
only when ``GET /me`` says the configured token carries ``vault:reveal``. An
advertised tool that always returns 403 is worse than a missing one: the model
will call it, read the refusal, and reasonably try again with different
arguments, because from where it sits a 403 is indistinguishable from a
mistake it made.
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
from cylist_mcp.client import ApiClient
from cylist_mcp.errors import CylistError

VAULT_REVEAL = "vault:reveal"

INSTRUCTIONS = """\
Cylist is a project manager: each project has a Kanban board, a people
directory, files and a vault of credentials.

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

A task can be split two ways. `create_subtask` makes a card of its own,
referenced `ATL-41-2` and addressable like any other task; `add_checklist_item`
makes a tick box that lives on the parent alone. Either way, every one of them
has to be finished or cancelled before the parent can be moved into the board's
last column — `move_task` refuses and names what is still outstanding.
"""


def build_server(client: ApiClient, scopes: frozenset[str]) -> MCPServer:
    """Wire the tools onto one API client, honouring the token's scopes."""
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

        return await _guard(call)

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

        return await _guard(call)

    # --- Tasks -------------------------------------------------------------

    @server.tool(
        name="list_tasks",
        description=(
            "List the cards on a project's board, in board order. Returns each "
            "task's reference (like 'ATL-41'), title, type, status, column, "
            "assignee, due date and who it is waiting on, plus the board's "
            "columns so you can tell which card is where. Optionally filter by "
            "status or assignee."
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

        return await _guard(call)

    @server.tool(
        name="get_task",
        description=(
            "Get one task with its whole timeline. Returns the task's fields and "
            "every comment and status change on it, oldest first — a status "
            "change carries the reason it was given and who it was waiting on."
        ),
    )
    async def get_task(
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"task": await client.get(f"/tasks/{task}")}

        return await _guard(call)

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

        return await _guard(call)

    @server.tool(
        name="create_task",
        description=(
            "Add a task to a project's board. It always lands at the bottom of "
            "the board's first column; call move_task afterwards if it belongs "
            "elsewhere. Title, description, type and assignee are required by the "
            "server: a card with no description or owner is the kind that goes "
            "stale. A due date is optional — leave it off rather than inventing "
            "one, because a card is only ever overdue against a date somebody "
            "actually chose. The assignee must already be a member of the "
            "project. Returns the created task, including its new reference."
        ),
    )
    async def create_task(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        title: Annotated[str, Field(description="One line naming the work.")],
        description: Annotated[str, Field(description="What done looks like.")],
        task_type: Annotated[str, Field(description="One of 'feature', 'bug' or 'chore'.")],
        assignee: Annotated[str, Field(description="Who owns it: a project member's name or id.")],
        due_date: Annotated[
            str | None,
            Field(description="ISO date, e.g. '2026-03-31'. Omit for a card with no date."),
        ] = None,
        jira_ref: Annotated[str | None, Field(description="Jira issue key, if any.")] = None,
        pr_ref: Annotated[str | None, Field(description="Pull request URL, if any.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("task_type", task_type, ("feature", "bug", "chore"))
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "type": task_type,
                "assignee_id": await resolve.person_id(client, assignee, project_ref=project),
            }
            if due_date:
                body["due_date"] = due_date
            if jira_ref:
                body["jira_ref"] = jira_ref
            if pr_ref:
                body["pr_ref"] = pr_ref
            return {"task": await client.post(f"/projects/{project}/tasks", body)}

        return await _guard(call)

    @server.tool(
        name="create_subtask",
        description=(
            "Split a task into a sub-task that gets its own card on the board. "
            "It is a task in every respect — first column, owner, its own due "
            "date — except its reference, which is numbered under its parent: a "
            "sub-task of ATL-41 is ATL-41-2, and is addressable by that "
            "everywhere a task reference is taken. Sub-tasks go one level deep, "
            "so splitting a sub-task again is refused. Use add_checklist_item "
            "instead when the piece needs no owner and no card of its own. "
            "Returns the created sub-task."
        ),
    )
    async def create_subtask(
        task: Annotated[
            str, Field(description="The parent task: a reference such as 'ATL-41', or its id.")
        ],
        title: Annotated[str, Field(description="One line naming the work.")],
        description: Annotated[str, Field(description="What done looks like.")],
        task_type: Annotated[str, Field(description="One of 'feature', 'bug' or 'chore'.")],
        assignee: Annotated[str, Field(description="Who owns it: a project member's name or id.")],
        due_date: Annotated[
            str | None,
            Field(description="ISO date, e.g. '2026-03-31'. Omit for a card with no date."),
        ] = None,
        jira_ref: Annotated[str | None, Field(description="Jira issue key, if any.")] = None,
        pr_ref: Annotated[str | None, Field(description="Pull request URL, if any.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("task_type", task_type, ("feature", "bug", "chore"))
            parent = await client.get(f"/tasks/{task}")
            project_ref = _project_of(parent)
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "type": task_type,
                "assignee_id": await resolve.person_id(client, assignee, project_ref=project_ref),
            }
            if due_date:
                body["due_date"] = due_date
            if jira_ref:
                body["jira_ref"] = jira_ref
            if pr_ref:
                body["pr_ref"] = pr_ref
            return {"task": await client.post(f"/tasks/{task}/subtasks", body)}

        return await _guard(call)

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

        return await _guard(call)

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

        return await _guard(call)

    @server.tool(
        name="move_task",
        description=(
            "Move a card to another column on the same board. The column may be "
            "named ('In progress') or given as an id; get_project lists them. "
            "Position counts from the top of the column and is clamped to its "
            "length, so 0 puts the card first and a large number puts it last. "
            "Moving does not change the task's status. A card with a sub-task "
            "still open cannot be moved into the board's last column; that comes "
            "back as an error naming what is outstanding. Returns the moved task."
        ),
    )
    async def move_task(
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
        column: Annotated[str, Field(description="Destination column name or id.")],
        position: Annotated[
            int, Field(description="Index from the top of the column. Default 0.", ge=0)
        ] = 0,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            current = await client.get(f"/tasks/{task}")
            column_id = await resolve.column_id(client, _project_of(current), column)
            moved = await client.post(
                f"/tasks/{task}/move", {"column_id": column_id, "position": position}
            )
            return {"task": moved}

        return await _guard(call)

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

        return await _guard(call)

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

        return await _guard(call)

    # --- People ------------------------------------------------------------

    @server.tool(
        name="list_people",
        description=(
            "List people. Cylist keeps one global directory; 'team' does the "
            "work and 'client' approves or unblocks it. Pass project to list "
            "only that project's members — which is the set an assignee or a "
            "waiting_on tag must come from. Returns each person's name, kind, "
            "role, responsibilities and email."
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

        return await _guard(call)

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

        return await _guard(call)

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

        return await _guard(call)

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

        return await _guard(call)

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

        return await _guard(call)

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
            "in 'timezone', which defaults to this machine's own zone rather than "
            "UTC. The report is deliberately concise: a card's moves appear as the "
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
                    "IANA zone the day is cut by, e.g. 'Asia/Kolkata'. Defaults to "
                    "the zone this machine is set to."
                )
            ),
        ] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            report = await client.get(
                f"/projects/{project}/reports/day",
                date=date,
                timezone=timezone or local_timezone(),
            )
            return {"report": report}

        return await _guard(call)

    if VAULT_REVEAL in scopes:
        _register_reveal(server, client)

    return server


def _register_reveal(server: MCPServer, client: ApiClient) -> None:
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

        return await _guard(call)


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


async def _guard(call: Callable[[], Awaitable[dict[str, Any]]]) -> CallToolResult:
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
