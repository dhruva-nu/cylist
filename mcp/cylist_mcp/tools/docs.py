"""Docs: what a project's agents ask before reading the code, and write back after."""

from __future__ import annotations

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp import resolve
from cylist_mcp.client import Api
from cylist_mcp.errors import CylistError
from cylist_mcp.tools.common import ProjectRef, as_tool_result

DOC_MAX_CHARS = 40_000
"""How much of a doc `read_doc` will return, for the same reason as a skill."""

LEARNED = "learned.md"
"""The doc in each topic that agents add what they learn to, one line at a time."""


def register(server: MCPServer, client: Api) -> None:
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
    async def list_docs(project: ProjectRef) -> CallToolResult:
        async def call() -> dict[str, Any]:
            return {"docs": await client.get(f"/projects/{project}/docs")}

        return await as_tool_result(call)

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

        return await as_tool_result(call)

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
        project: ProjectRef,
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

        return await as_tool_result(call)

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
        project: ProjectRef,
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

        return await as_tool_result(call)

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
        project: ProjectRef,
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
                return await edit_doc(client, project, doc, title=title, body=body, append=append)
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
                raise with_topics_listed(exc) from exc

        return await as_tool_result(call)


async def edit_doc(
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


def with_topics_listed(exc: CylistError) -> CylistError:
    """A topic refusal with the topics written into its message.

    The server lists them in the error's details, but the message is what a
    model reads first, and a refusal that says "name a topic" without saying
    which ones there are costs it a list_docs to recover from.
    """
    # Read defensively: these details came off the wire, and a shape this
    # does not expect should leave the server's own refusal as it was rather
    # than turn it into a crash the model cannot read.
    raw = exc.details.get("topics")
    topics = [topic for topic in raw if isinstance(topic, dict)] if isinstance(raw, list) else []
    if exc.code != "topic_unclear" or not topics:
        return exc

    listed = "; ".join(f"{topic.get('section')} / {topic.get('name')}" for topic in topics)
    return CylistError(
        f"{exc.message} Topics: {listed}. Call write_doc again with `topic` set to one.",
        code=exc.code,
        status_code=exc.status_code,
        details=exc.details,
    )
