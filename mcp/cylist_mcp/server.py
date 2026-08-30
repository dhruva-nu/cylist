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
from collections.abc import Awaitable, Callable
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
        name="create_task",
        description=(
            "Add a task to a project's board. It always lands at the bottom of "
            "the board's first column; call move_task afterwards if it belongs "
            "elsewhere. Every argument except jira_ref and pr_ref is required by "
            "the server: a card with no description, due date or owner is the "
            "kind that goes stale. The assignee must already be a member of the "
            "project. Returns the created task, including its new reference."
        ),
    )
    async def create_task(
        project: Annotated[str, Field(description="Project key such as 'ATL', or its id.")],
        title: Annotated[str, Field(description="One line naming the work.")],
        description: Annotated[str, Field(description="What done looks like.")],
        task_type: Annotated[str, Field(description="One of 'feature', 'bug' or 'chore'.")],
        due_date: Annotated[str, Field(description="ISO date, e.g. '2026-03-31'.")],
        assignee: Annotated[str, Field(description="Who owns it: a project member's name or id.")],
        jira_ref: Annotated[str | None, Field(description="Jira issue key, if any.")] = None,
        pr_ref: Annotated[str | None, Field(description="Pull request URL, if any.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            _check_choice("task_type", task_type, ("feature", "bug", "chore"))
            body: dict[str, Any] = {
                "title": title,
                "description": description,
                "type": task_type,
                "due_date": due_date,
                "assignee_id": await resolve.person_id(client, assignee, project_ref=project),
            }
            if jira_ref:
                body["jira_ref"] = jira_ref
            if pr_ref:
                body["pr_ref"] = pr_ref
            return {"task": await client.post(f"/projects/{project}/tasks", body)}

        return await _guard(call)

    @server.tool(
        name="move_task",
        description=(
            "Move a card to another column on the same board. The column may be "
            "named ('In progress') or given as an id; get_project lists them. "
            "Position counts from the top of the column and is clamped to its "
            "length, so 0 puts the card first and a large number puts it last. "
            "Moving does not change the task's status. Returns the moved task."
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
            "required for 'hold' and 'blocked' and is written to the task's "
            "timeline, so say what is actually in the way rather than restating "
            "the status. waiting_on names the people the work now waits on — "
            "they must be project members — and is cleared when the task goes "
            "back to 'active'. Returns the updated task with its new timeline."
        ),
    )
    async def set_task_status(
        task: Annotated[str, Field(description="Task reference such as 'ATL-41', or its id.")],
        status: Annotated[str, Field(description="One of 'active', 'hold' or 'blocked'.")],
        reason: Annotated[
            str | None,
            Field(description="Why. Required for 'hold' and 'blocked'."),
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
    same sentence with a UUID in it is not. Keys cannot contain a dash.
    """
    key = str(task.get("reference", "")).rsplit("-", 1)[0]
    return key or str(task["project_id"])


def _check_status(status: str) -> None:
    _check_choice("status", status, ("active", "hold", "blocked"))


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
