"""Asking the person a question on the card, and waiting for their answer."""

from __future__ import annotations

import asyncio
import contextlib
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.types import CallToolResult
from pydantic import Field

from cylist_mcp.client import Api
from cylist_mcp.tools.common import as_tool_result

ASK_POLL_SECONDS = 20
"""How long one poll for an answer waits on the server. Inside the client's
thirty-second request timeout, and frequent enough that the server can tell a
tool still waiting from one that has gone — it gives up on a question nobody
has polled for ninety seconds."""

WITHDRAW_TIMEOUT_SECONDS = 5
"""How long withdrawing an abandoned question may hold up the cancellation."""


def register(server: MCPServer, client: Api) -> None:
    @server.tool(
        name="ask_human",
        description=(
            "Ask the person you are working for a question, on the card your "
            "session is bound to, and wait for the answer. Use it when you "
            "cannot reasonably decide alone — a product choice, an ambiguity in "
            "the card, something only they know — not to report progress. The "
            "card turns amber with your question and its options; they answer "
            "from the board, and the answer is this tool's result, so you carry "
            "on in the same turn. Offer 2-4 short options when the choice is "
            "between known alternatives; they may also type a reply. If nobody "
            "answers within the timeout the result says so: decide yourself "
            "and say what you chose, or ask again in the terminal. Needs a "
            "session bound to the card (/work)."
        ),
    )
    async def ask_human(
        task: Annotated[
            str,
            Field(description="The card your session is bound to, such as 'ATL-41'."),
        ],
        question: Annotated[
            str, Field(description="One clear question, as you would ask a colleague.")
        ],
        options: Annotated[
            list[str] | None,
            Field(description="Up to six short choices, in the order to show them."),
        ] = None,
        timeout_minutes: Annotated[
            int,
            Field(
                description="How long to wait for an answer. Default 30, at most 240.",
                ge=1,
                le=240,
            ),
        ] = 30,
    ) -> CallToolResult:
        async def call() -> dict[str, Any]:
            asked = await client.post(
                f"/tasks/{task}/agent-prompts",
                {
                    "question": question,
                    "options": options or [],
                    "timeout_seconds": max(60, timeout_minutes * 60),
                },
            )
            settled = await wait_for_answer(client, task, str(asked["id"]), asked)
            return answer_payload(settled, timeout_minutes)

        return await as_tool_result(call)


async def wait_for_answer(
    client: Api, task: str, prompt_id: str, current: dict[str, Any]
) -> dict[str, Any]:
    """Long-poll a prompt until it is no longer open.

    The server ends each poll as soon as the prompt closes, so an answer
    arrives within moments of being given. If this call is cancelled — the
    person pressed Esc, the session went away — the question is withdrawn on
    the way out, so the card does not go on asking for an answer nobody will
    read.
    """
    path = f"/tasks/{task}/agent-prompts/{prompt_id}"
    try:
        while current.get("status") == "open":
            current = await client.get(path, wait=ASK_POLL_SECONDS)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            async with asyncio.timeout(WITHDRAW_TIMEOUT_SECONDS):
                await client.post(f"{path}/withdraw", {"reason": "interrupted"})
        raise
    return current


def answer_payload(prompt: dict[str, Any], timeout_minutes: int) -> dict[str, Any]:
    """What the model is handed back: the answer, or plainly that there is none."""
    status = prompt.get("status")
    if status == "answered":
        return {
            "answered": True,
            "answer": prompt.get("answer"),
            "option": prompt.get("answer_key"),
            "answered_by": prompt.get("answered_by"),
        }
    if status == "expired":
        why = (
            f"Nobody answered within {timeout_minutes} minutes. Decide for yourself "
            "and say what you chose, or ask again in the terminal."
        )
    else:
        why = (
            "The question was withdrawn before anybody answered it: "
            + str(prompt.get("closed_reason") or "the session ended").replace("_", " ")
            + "."
        )
    return {"answered": False, "status": status, "message": why}
