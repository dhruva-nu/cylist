"""Names to ids, for tools that would otherwise demand a UUID.

A model calling ``move_task`` has just read a board and knows the column is
called "In progress". Making it find a UUID first is an extra round trip and an
extra thing to get wrong, so every id-shaped argument accepts a name too.

An ambiguous name is an error rather than a guess, and the error names the
candidates — a model can recover from that in one turn, but it cannot recover
from a card silently moved to the wrong column.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

from cylist_mcp.client import Api
from cylist_mcp.errors import CylistError

JsonDict = dict[str, Any]


def is_uuid(value: str) -> bool:
    try:
        UUID(value)
    except ValueError:
        return False
    return True


def pick(candidates: Sequence[JsonDict], name: str, *, kind: str, where: str) -> JsonDict:
    """The one candidate called ``name``: exact, then prefix, then substring."""
    wanted = name.strip().casefold()
    if not wanted:
        raise CylistError(f"An empty {kind} name cannot be resolved.", code="ambiguous")

    def named(item: JsonDict) -> str:
        return str(item.get("name", ""))

    passes = (
        [item for item in candidates if named(item).casefold() == wanted],
        [item for item in candidates if named(item).casefold().startswith(wanted)],
        [item for item in candidates if wanted in named(item).casefold()],
    )

    for found in passes:
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            names = sorted(named(item) for item in found)
            raise CylistError(
                f"{name!r} matches more than one {kind} in {where}: {', '.join(names)}. "
                "Call again with the full name or the id.",
                code="ambiguous",
                details={"candidates": names},
            )

    known = [named(item) for item in candidates]
    raise CylistError(
        f"No {kind} called {name!r} in {where}. These exist: {', '.join(known) or 'none'}.",
        code="not_found",
        details={"known": known},
    )


async def person_id(client: Api, name: str, *, project_ref: str) -> str:
    """A project member's id, from their name."""
    if is_uuid(name):
        return name
    members = (await client.get(f"/projects/{project_ref}/members")).get("members", [])
    return str(pick(members, name, kind="person", where=f"{project_ref}'s members")["id"])


async def column_id(client: Api, project_ref: str, name: str) -> str:
    """A board column's id, from its name."""
    if is_uuid(name):
        return name
    columns = (await client.get(f"/projects/{project_ref}/columns")).get("columns", [])
    return str(pick(columns, name, kind="column", where=f"{project_ref}'s board")["id"])


async def goal_ref(client: Api, project_ref: str, name: str) -> str:
    """A goal's reference, from its name — or from a reference or id, unchanged.

    Reference first, because ``ATL-G1`` is what every goal tool hands back and
    what a model is most likely to be holding; a name is matched against the
    project's goals the way a column's is against its board.
    """
    if is_uuid(name):
        return name

    goals: list[JsonDict] = list(await client.get(f"/projects/{project_ref}/goals"))
    wanted = name.strip().casefold()
    for goal in goals:
        if str(goal.get("reference", "")).casefold() == wanted:
            return str(goal["reference"])
    return str(pick(goals, name, kind="goal", where=f"{project_ref}'s goals")["reference"])


async def folder_id(client: Api, project_ref: str, path: str) -> str:
    """Walk ``Contracts/2026`` down the project's folder tree to an id."""
    if is_uuid(path):
        return path

    segments = [segment for segment in path.split("/") if segment]
    if not segments:
        raise CylistError("Give a folder path, such as 'Contracts/2026'.", code="not_found")

    level: list[JsonDict] = list(await client.get(f"/projects/{project_ref}/tree"))
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

    return str(current["id"])


async def vault_node(client: Api, project_ref: str, path: str) -> tuple[JsonDict, JsonDict]:
    """Resolve ``Logins/Billing/Stripe`` to its tree and node."""
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        raise CylistError(
            f"{path!r} needs a tree and at least one node, e.g. 'Logins/Stripe'.",
            code="not_found",
        )

    trees = await client.get(f"/projects/{project_ref}/vault/trees")
    summary = pick(list(trees), segments[0], kind="vault tree", where=project_ref)
    tree = await client.get(f"/vault/trees/{summary['id']}")

    level: list[JsonDict] = list(tree.get("nodes", []))
    walked = [str(tree.get("name", segments[0]))]
    node: JsonDict = {}

    for segment in segments[1:]:
        node = pick(level, segment, kind="vault node", where="/".join(walked))
        walked.append(str(node.get("name", segment)))
        children = node.get("children", [])
        level = children if isinstance(children, list) else []

    return tree, node


def doc_topics(tree: JsonDict) -> list[JsonDict]:
    """Every topic in a docs tree, named as ``Engineering / MCP``.

    Named by section as well as by topic, because a name is unique only within
    its section: "Tasks" can be a Product topic and an Engineering one.
    """
    return [
        {**topic, "name": f"{part['label']} / {topic['name']}", "topic_name": topic["name"]}
        for part in tree.get("sections", [])
        for topic in part.get("topics", [])
    ]


async def doc_topic_id(client: Api, project_ref: str, name: str) -> str:
    """A doc topic's id, from ``Engineering / MCP``, from ``MCP``, or from its id."""
    if is_uuid(name):
        return name
    topics = doc_topics(await client.get(f"/projects/{project_ref}/docs"))
    try:
        return str(pick(topics, name, kind="doc topic", where=f"{project_ref}'s docs")["id"])
    except CylistError as exc:
        if exc.code != "not_found":
            raise
        raise CylistError(
            f"{exc.message} Topics are made by people, not agents: file the doc under one "
            "of these — `place_doc` says which.",
            code=exc.code,
            details=exc.details,
        ) from exc


async def doc_id(client: Api, project_ref: str | None, ref: str) -> str:
    """A doc's id, from its id or its path: ``Engineering / MCP / learned.md``.

    The topic may be left off — ``learned.md`` alone — when the title names one
    doc on the project; the section may be too, ``MCP / learned.md``.
    """
    if is_uuid(ref):
        return ref
    if not project_ref:
        raise CylistError(
            f"{ref!r} is not a doc id, so name the project it is on as well.",
            code="validation_failed",
        )

    tree = await client.get(f"/projects/{project_ref}/docs")
    topic_path, _, title = ref.rpartition("/")
    title, topic_path = title.strip(), topic_path.strip()
    topics = doc_topics(tree)
    if topic_path:
        chosen = pick(topics, topic_path, kind="doc topic", where=f"{project_ref}'s docs")
        topics = [chosen]
    docs = [
        {**doc, "name": doc["title"], "path": f"{topic['name']} / {doc['title']}"}
        for topic in topics
        for doc in topic.get("docs", [])
    ]
    where = topics[0]["name"] if topic_path else f"{project_ref}'s docs"
    found = [doc for doc in docs if doc["title"].casefold() == title.casefold()]
    if len(found) > 1:
        paths = sorted(doc["path"] for doc in found)
        raise CylistError(
            f"{title!r} is the title of more than one doc: {', '.join(paths)}. "
            "Call again with its topic, e.g. 'Engineering / MCP / learned.md', or its id.",
            code="ambiguous",
            details={"candidates": paths},
        )
    return str(pick(docs, title, kind="doc", where=where)["id"])
