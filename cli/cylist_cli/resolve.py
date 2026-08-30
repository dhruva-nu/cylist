"""Turning the names a human types into the ids the API wants.

The API is addressed by UUID for people, columns, folders and vault nodes.
Nobody says "move ATL-41 to 0192f3c4-…"; they say "move it to In progress". So
every place a command takes a name, it comes through here.

Two properties matter more than cleverness:

* **A near-miss is never guessed at.** Two candidates means an error listing
  both, not the first one. An agent that silently moved a card to the wrong
  column would be worse than one that stopped.
* **A UUID always wins.** Anything that parses as a UUID is passed through
  untouched, so a script that already holds ids never pays for the lookup and
  never trips over a person whose name matches two records.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from cylist_cli.client import Client
from cylist_cli.errors import CylistError

JsonDict = dict[str, Any]


def is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def pick(candidates: Sequence[JsonDict], name: str, *, kind: str, where: str) -> JsonDict:
    """Find the one record called ``name``, or explain why there isn't one.

    Matching widens in three steps — exact, then prefix, then substring — and
    stops at the first step that finds anything. "Lena" beats "Lena Weiss"
    only if nothing matched exactly, which keeps a full name from being
    ambiguous with itself when someone else's name contains it.
    """
    wanted = name.strip().casefold()
    if not wanted:
        raise CylistError(f"An empty {kind} name is not a name.")

    for match in (_exact, _prefix, _substring):
        found = match(candidates, wanted)
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = ", ".join(sorted(_name(item) for item in found))
            raise CylistError(
                f"{name!r} matches more than one {kind} in {where}: {names}. "
                "Use the full name or the id.",
                details={"candidates": [_name(item) for item in found]},
            )

    known = ", ".join(_name(item) for item in candidates) or "none"
    raise CylistError(
        f"No {kind} called {name!r} in {where}. Known {kind}s: {known}.",
        details={"known": [_name(item) for item in candidates]},
    )


def _name(item: JsonDict) -> str:
    return str(item.get("name", ""))


def _exact(candidates: Sequence[JsonDict], wanted: str) -> list[JsonDict]:
    return [item for item in candidates if _name(item).casefold() == wanted]


def _prefix(candidates: Sequence[JsonDict], wanted: str) -> list[JsonDict]:
    return [item for item in candidates if _name(item).casefold().startswith(wanted)]


def _substring(candidates: Sequence[JsonDict], wanted: str) -> list[JsonDict]:
    return [item for item in candidates if wanted in _name(item).casefold()]


# --- People ----------------------------------------------------------------


def person_id(client: Client, name: str, *, project_ref: str | None = None) -> str:
    """The id of the person called ``name``.

    Scoped to a project's members when one is given, because that is the set
    the API will accept for an assignee or a ``waiting_on`` tag — resolving
    against the whole directory would turn a helpful "not a member of ATL"
    into a puzzling 422 from the server.
    """
    if is_uuid(name):
        return name

    if project_ref is None:
        candidates = _people(client.get("/people"))
        where = "the people directory"
    else:
        candidates = _people(client.get(f"/projects/{project_ref}/members").get("members", []))
        where = f"{project_ref}'s members"

    return str(pick(candidates, name, kind="person", where=where)["id"])


def _people(payload: Any) -> list[JsonDict]:
    return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []


# --- Board columns ---------------------------------------------------------


def column_id(client: Client, project_ref: str, name: str) -> str:
    """The id of the column called ``name`` on this project's board."""
    if is_uuid(name):
        return name
    board = client.get(f"/projects/{project_ref}/columns")
    columns = board.get("columns", []) if isinstance(board, dict) else []
    return str(pick(columns, name, kind="column", where=f"{project_ref}'s board")["id"])


# --- Folders and files -----------------------------------------------------


def split_path(path: str) -> list[str]:
    """Split ``Contracts/2026/msa.pdf`` into its segments, ignoring extra slashes."""
    return [segment for segment in path.split("/") if segment]


def folder(client: Client, project_ref: str, path: str) -> JsonDict:
    """Walk a slash-separated path down the project's folder tree.

    Folders are addressed by id in the API and by name on the screen, and the
    CLI is the only place that has to bridge the two. It walks the tree the
    project returns in one request rather than asking per level.
    """
    segments = split_path(path)
    if not segments:
        raise CylistError("Give a folder path, such as 'Contracts/2026'.")

    tree = client.get(f"/projects/{project_ref}/tree")
    level: list[JsonDict] = tree if isinstance(tree, list) else []
    walked: list[str] = []
    current: JsonDict = {}

    for segment in segments:
        current = pick(
            level,
            segment,
            kind="folder",
            where="/".join(walked) or f"the top level of {project_ref}",
        )
        walked.append(str(current.get("name", segment)))
        children = current.get("children", [])
        level = children if isinstance(children, list) else []

    return current


def item(client: Client, project_ref: str, path: str) -> JsonDict:
    """Find one file or link by its path, e.g. ``Contracts/2026/msa.pdf``."""
    segments = split_path(path)
    if not segments:
        raise CylistError("Give a file path, such as 'Contracts/2026/msa.pdf'.")

    name = segments[-1]
    parent = segments[:-1]
    if not parent:
        raise CylistError(
            f"{name!r} has no folder in front of it. Files live in folders, so a path "
            "needs at least one, such as 'Contracts/msa.pdf'."
        )

    holder = folder(client, project_ref, "/".join(parent))
    listing = client.get(f"/folders/{holder['id']}/children")
    items = listing.get("items", []) if isinstance(listing, dict) else []
    return pick(items, name, kind="file", where="/".join(parent))


# --- Vault -----------------------------------------------------------------


def vault_node(client: Client, project_ref: str, path: str) -> tuple[JsonDict, JsonDict]:
    """Resolve ``Logins/Billing/Stripe`` to its tree and its node.

    The first segment names a tree, the rest walk the branches inside it.
    Returns both because callers want the tree's name for the confirmation
    line as well as the node's id for the request.
    """
    segments = split_path(path)
    if not segments:
        raise CylistError("Give a vault path, such as 'Logins/Billing/Stripe'.")

    trees = client.get(f"/projects/{project_ref}/vault/trees")
    tree_summary = pick(
        trees if isinstance(trees, list) else [],
        segments[0],
        kind="vault tree",
        where=project_ref,
    )
    tree = client.get(f"/vault/trees/{tree_summary['id']}")

    nodes = tree.get("nodes", []) if isinstance(tree, dict) else []
    level: list[JsonDict] = nodes if isinstance(nodes, list) else []
    walked = [str(tree.get("name", segments[0]))]
    node: JsonDict = {}

    for segment in segments[1:]:
        node = pick(level, segment, kind="vault node", where="/".join(walked))
        walked.append(str(node.get("name", segment)))
        children = node.get("children", [])
        level = children if isinstance(children, list) else []

    if not node:
        raise CylistError(
            f"{path!r} names the tree {tree.get('name')!r} but no node inside it. "
            "Add the node, e.g. 'Logins/Stripe'."
        )

    return tree, node
