"""``ask_human``: a question on the card, and a turn held open for the answer.

Driven against a scripted transport rather than the shared fake, because what
matters here is a *sequence* — asked, still open, answered — and the timing of
a cancellation in the middle of it.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
import pytest_asyncio
from mcp.server.mcpserver import MCPServer

from cylist_mcp.client import ApiClient
from cylist_mcp.server import build_server
from tests.conftest import call

PROMPT_ID = "0192f3c4-0009-7000-8000-000000000001"
PROMPTS = "/api/v1/tasks/ATL-41/agent-prompts"


def prompt(**changes: Any) -> dict[str, Any]:
    return {
        "id": PROMPT_ID,
        "kind": "question",
        "text": "Grey dot, or no dot?",
        "options": [{"key": "1", "label": "Grey"}, {"key": "2", "label": "None"}],
        "status": "open",
        "answer": None,
        "answer_key": None,
        "answered_by": None,
        "closed_reason": None,
        **changes,
    }


class Script:
    """A server that answers the polls from a list, and records every request."""

    def __init__(self, polls: list[dict[str, Any]] | None = None) -> None:
        self.polls = list(polls or [])
        self.requests: list[httpx.Request] = []
        self.hang = asyncio.Event()
        self.polling = asyncio.Event()

    async def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "POST" and path == PROMPTS:
            return httpx.Response(201, json=prompt())
        if request.method == "POST" and path.endswith("/withdraw"):
            return httpx.Response(200, json=prompt(status="withdrawn"))
        if request.method == "GET" and path == f"{PROMPTS}/{PROMPT_ID}":
            if not self.polls:
                self.polling.set()
                await self.hang.wait()  # a long-poll with nothing to say yet
            return httpx.Response(200, json=self.polls.pop(0))
        return httpx.Response(404, json={"error": {"code": "not_found", "message": path}})

    def sent(self, method: str, suffix: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and r.url.path.endswith(suffix)]


@pytest_asyncio.fixture
async def serve() -> AsyncIterator[Callable[[Script], MCPServer]]:
    clients: list[ApiClient] = []

    def build(script: Script) -> MCPServer:
        client = ApiClient(
            "http://cylist.test", "cyl_test_token", transport=httpx.MockTransport(script)
        )
        clients.append(client)
        return build_server(client, frozenset({"read", "write"}))

    yield build
    for client in clients:
        await client.aclose()


async def test_it_asks_then_waits_until_somebody_answers(
    serve: Callable[[Script], MCPServer],
) -> None:
    script = Script(
        [prompt(), prompt(status="answered", answer="Grey", answer_key="1", answered_by="Dhruva")]
    )

    result = await call(
        serve(script),
        "ask_human",
        task="ATL-41",
        question="Grey dot, or no dot?",
        options=["Grey", "None"],
        timeout_minutes=10,
    )

    assert not result.is_error
    assert result.data == {
        "answered": True,
        "answer": "Grey",
        "option": "1",
        "answered_by": "Dhruva",
    }
    asked = json.loads(script.sent("POST", "/agent-prompts")[0].content)
    assert asked == {
        "question": "Grey dot, or no dot?",
        "options": ["Grey", "None"],
        "timeout_seconds": 600,
    }
    polls = script.sent("GET", PROMPT_ID)
    assert len(polls) == 2
    assert all(poll.url.params["wait"] == "20" for poll in polls)


async def test_nobody_answering_is_a_result_the_agent_can_act_on(
    serve: Callable[[Script], MCPServer],
) -> None:
    script = Script([prompt(status="expired")])

    result = await call(serve(script), "ask_human", task="ATL-41", question="Which?")

    assert not result.is_error
    assert result.data["answered"] is False
    assert "Nobody answered within 30 minutes" in result.data["message"]


async def test_a_session_that_ended_says_so(serve: Callable[[Script], MCPServer]) -> None:
    script = Script([prompt(status="withdrawn", closed_reason="session_ended")])

    result = await call(serve(script), "ask_human", task="ATL-41", question="Which?")

    assert result.data["message"].endswith("session ended.")


async def test_no_bound_session_is_an_error_with_the_way_out() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            409,
            json={
                "error": {
                    "code": "conflict",
                    "message": "No session of yours is open on ATL-41 (/work ATL-41).",
                    "details": {},
                }
            },
        )

    async with ApiClient(
        "http://cylist.test", "t", transport=httpx.MockTransport(refuse)
    ) as client:
        result = await call(
            build_server(client, frozenset({"read", "write"})),
            "ask_human",
            task="ATL-41",
            question="Which?",
        )

    assert result.is_error
    assert "/work ATL-41" in result.text


async def test_a_cancelled_call_withdraws_its_question(
    serve: Callable[[Script], MCPServer],
) -> None:
    script = Script()
    server = serve(script)

    asking = asyncio.create_task(server.call_tool("ask_human", {"task": "ATL-41", "question": "?"}))
    await asyncio.wait_for(script.polling.wait(), timeout=2)
    asking.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asking

    withdrawn = script.sent("POST", "/withdraw")
    assert len(withdrawn) == 1
    assert json.loads(withdrawn[0].content) == {"reason": "interrupted"}
