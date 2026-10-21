"""The vault: a project's credentials, filed as trees of branches and secrets.

**A tool that cannot work is not offered.** ``reveal_secret`` is registered
only when ``GET /me`` says the configured token carries ``vault:reveal``, and
``add_secret`` / ``update_secret`` only when it carries ``write`` and
``vault:read``. An advertised tool that always returns 403 is worse than a
missing one: the model will call it, read the refusal, and reasonably try again
with different arguments, because from where it sits a 403 is
indistinguishable from a mistake it made.
"""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp import resolve
from cylist_mcp.client import Api, every
from cylist_mcp.errors import CylistError
from cylist_mcp.tools.common import ProjectRef, as_tool_result, check_choice

VAULT_REVEAL = "vault:reveal"

VAULT_WRITE = frozenset({"write", "vault:read"})
"""What the API asks of a vault write: ``write``, and ``vault:read`` to see
where in the tree it lands."""

SENSITIVITIES = ("public", "internal", "restricted")


def register(server: MCPServer, client: Api) -> None:
    """Add ``list_vault``, which every token may call: it never carries a value."""

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
        project: ProjectRef,
        tree: Annotated[str | None, Field(description="A tree's name, e.g. 'Logins'.")] = None,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            trees = await every(client, f"/projects/{project}/vault/trees")
            if not tree:
                return {"trees": trees}
            summary = resolve.pick(trees, tree, kind="vault tree", where=project)
            return {"tree": await client.get(f"/vault/trees/{summary['id']}")}

        return await as_tool_result(call)


def register_writes(server: MCPServer, client: Api) -> None:
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
        project: ProjectRef,
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
                check_choice("sensitivity", sensitivity, SENSITIVITIES)
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

        return await as_tool_result(call)

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
        project: ProjectRef,
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
                check_choice("sensitivity", sensitivity, SENSITIVITIES)
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
            # Field names only: the value itself must never be echoed back.
            return {"secret": updated, "changed": sorted({*secret, *body} - {"secret"})}

        return await as_tool_result(call)


def register_reveal(server: MCPServer, client: Api) -> None:
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
        project: ProjectRef,
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

        return await as_tool_result(call)
