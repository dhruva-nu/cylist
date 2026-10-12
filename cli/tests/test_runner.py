"""``cylist run``: the agent through the Agent SDK, with the board as its UI.

No network and no Claude: the socket is a fake that answers like the server,
and ``ClaudeSDKClient`` is a fake that replays a turn and asks permission the
way the SDK does, through ``can_use_tool``.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator, Callable
from typing import Any, ClassVar

import httpx
import pytest
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)
from claude_agent_sdk.types import PermissionRuleValue, PermissionUpdate

from cylist_cli import config, runner
from cylist_cli.client import Client
from cylist_cli.context import Context
from cylist_cli.errors import CylistError
from tests import fake_api
from tests.conftest import Runner


def frames(sent: list[str]) -> list[dict[str, Any]]:
    return [json.loads(frame) for frame in sent]


def levels(sent: list[str]) -> list[tuple[str, str | None, str | None]]:
    return [
        (f["report"]["state"], f["report"]["reason"], f["report"]["note"])
        for f in frames(sent)
        if f["type"] == "state"
    ]


def assistant(*blocks: Any) -> dict[str, Any]:
    record = runner.record_of(AssistantMessage(content=list(blocks), model="m"))
    assert record is not None
    return record


BASH = ToolUseBlock(id="t1", name="Bash", input={"command": "git push origin staging"})


# --- Bridge ----------------------------------------------------------------------


def test_a_tool_call_becomes_the_now_line_and_a_feed_line() -> None:
    sent: list[str] = []
    bridge = runner.Bridge("ATL-1", sent.append)

    bridge.saw(assistant(TextBlock("Pushing now."), BASH))

    assert levels(sent) == [("working", None, "Running git push origin staging")]
    [feed] = [f for f in frames(sent) if f["type"] == "feed"]
    assert [(e["kind"], e["text"]) for e in feed["entries"]] == [
        ("said", "Pushing now."),
        ("run", "git push origin staging"),
    ]


def test_a_waiting_permission_holds_the_level_through_a_parallel_tool_call() -> None:
    """A working level would tell the server the terminal answered, and close it."""

    async def scenario() -> tuple[dict[str, Any] | None, list[str]]:
        sent: list[str] = []
        bridge = runner.Bridge("ATL-1", sent.append)
        asking = asyncio.ensure_future(bridge.ask("Bash", BASH.input, wait=5))
        await asyncio.sleep(0)
        bridge.saw(assistant(ToolUseBlock(id="t2", name="Read", input={"file_path": "/a/b.py"})))
        [prompt] = [f for f in frames(sent) if f["type"] == "prompt"]
        bridge.heard(
            {
                "type": "answer",
                "prompt_id": prompt["prompt"]["id"],
                "status": "answered",
                "key": "allow_once",
                "answer": "Allow once",
            }
        )
        return await asking, sent

    answer, sent = asyncio.run(scenario())

    assert answer is not None and answer["key"] == "allow_once"
    assert levels(sent) == [
        ("waiting", "permission", "Running git push origin staging"),
        ("working", None, "Reading b.py"),
    ]


def test_a_permission_nobody_answers_is_withdrawn_as_expired() -> None:
    sent: list[str] = []

    async def scenario() -> dict[str, Any] | None:
        return await runner.Bridge("ATL-1", sent.append).ask("Bash", BASH.input, wait=0.01)

    assert asyncio.run(scenario()) is None
    [withdraw] = [f for f in frames(sent) if f["type"] == "withdraw"]
    assert withdraw["reason"] == "expired"


def test_a_reconnect_resends_the_level_and_the_same_open_prompt() -> None:
    async def scenario() -> list[str]:
        sent: list[str] = []
        bridge = runner.Bridge("ATL-1", sent.append)
        asking = asyncio.ensure_future(bridge.ask("Bash", BASH.input, wait=5))
        await asyncio.sleep(0)
        bridge.resync()
        asking.cancel()
        return sent

    found = frames(asyncio.run(scenario()))
    prompts = [f["prompt"]["id"] for f in found if f["type"] == "prompt"]
    assert len(prompts) == 2 and prompts[0] == prompts[1]
    assert found[-2]["type"] == "withdraw" and found[-2]["reason"] == "interrupted"


def test_a_subagents_messages_and_a_persons_prompt_are_not_the_agents() -> None:
    sub = AssistantMessage(content=[TextBlock("x")], model="m", parent_tool_use_id="t9")
    assert runner.record_of(sub) is None
    assert runner.record_of(UserMessage(content="please fix it")) is None
    record = runner.record_of(
        UserMessage(content=[ToolResultBlock(tool_use_id="t1", content="ok")])
    )
    assert record is not None
    assert record["type"] == "user"
    assert record["message"] == {
        "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "ok", "is_error": None}]
    }


# --- The decision ----------------------------------------------------------------


def answered(key: str) -> dict[str, Any]:
    return {"type": "answer", "status": "answered", "key": key, "answer": key}


def test_allow_once_and_deny() -> None:
    assert runner.decision(answered("allow_once"), "Bash", {}, []) == PermissionResultAllow()
    denied = runner.decision(answered("deny"), "Bash", {}, [])
    assert isinstance(denied, PermissionResultDeny)
    assert "board" in denied.message


def test_no_answer_is_a_denial_since_there_is_no_terminal_to_ask() -> None:
    assert isinstance(runner.decision(None, "Bash", {}, []), PermissionResultDeny)


def test_allow_for_this_session_narrows_suggestions_to_the_session() -> None:
    rule = PermissionRuleValue(tool_name="Bash", rule_content="npm test")
    suggestions = [
        PermissionUpdate(
            type="addRules", rules=[rule], behavior="allow", destination="localSettings"
        ),
        PermissionUpdate(type="setMode", mode="acceptEdits", destination="session"),
    ]

    verdict = runner.decision(answered("allow_session"), "Bash", {}, suggestions)

    assert isinstance(verdict, PermissionResultAllow)
    assert verdict.updated_permissions == [
        PermissionUpdate(type="addRules", rules=[rule], behavior="allow", destination="session")
    ]


def test_allow_for_this_session_without_suggestions_allows_that_exact_command() -> None:
    verdict = runner.decision(answered("allow_session"), "Bash", BASH.input, [])

    assert isinstance(verdict, PermissionResultAllow)
    [update] = verdict.updated_permissions or []
    assert update.destination == "session"
    assert update.rules == [
        PermissionRuleValue(tool_name="Bash", rule_content="git push origin staging")
    ]


# --- ask_human -------------------------------------------------------------------


def test_ask_human_names_its_session_and_waits_for_the_answer() -> None:
    recorder = fake_api.Recorder()
    base = "/tasks/ATL-1/agent-prompts"
    transport = fake_api.build(
        recorder,
        overrides={
            ("POST", base): httpx.Response(201, json={"id": "p1", "status": "open"}),
            ("GET", f"{base}/p1"): httpx.Response(
                200, json={"id": "p1", "status": "answered", "answer": "Yes", "answer_key": "1"}
            ),
        },
    )
    http = Client("http://cylist.test", "cyl_test_token", transport=transport)

    payload = asyncio.run(runner.ask_question(http, "ATL-1", "sess-1", "Ship it?", ["Yes"], 5))

    assert payload["answered"] is True and payload["answer"] == "Yes"
    posted = json.loads(recorder.requests[0].content)
    assert posted["client_session_id"] == "sess-1"
    assert posted["timeout_seconds"] == 300
    assert recorder.requests[1].url.params["wait"] == str(runner.ASK_POLL_SECONDS)


def test_a_question_nobody_answered_tells_the_agent_to_decide() -> None:
    payload = runner.answer_payload({"status": "expired"}, 30)
    assert payload["answered"] is False
    assert "Decide for yourself" in payload["message"]


# --- A whole run -----------------------------------------------------------------


class FakeSocket:
    """Answers like the server: an ack per level, and a board that answers prompts."""

    def __init__(self, board: Callable[[dict[str, Any]], dict[str, Any] | None]) -> None:
        self.sent: list[str] = []
        self._board = board
        self._incoming: asyncio.Queue[str | None] = asyncio.Queue()

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def send(self, frame: str) -> None:
        self.sent.append(frame)
        parsed = json.loads(frame)
        if parsed["type"] == "state":
            await self._incoming.put(json.dumps({"type": "ack", "session": {}}))
        reply = self._board(parsed)
        if reply is not None:
            await self._incoming.put(json.dumps(reply))
        if parsed["type"] == "bye":
            await self._incoming.put(None)

    def __aiter__(self) -> AsyncIterator[str]:
        return self

    async def __anext__(self) -> str:
        frame = await self._incoming.get()
        if frame is None:
            raise StopAsyncIteration
        return frame


class FakeAgent:
    """Replays one turn: a Bash call that needs permission, its result, a reply."""

    verdicts: ClassVar[list[Any]] = []

    def __init__(self, options: ClaudeAgentOptions) -> None:
        self.options = options
        self.prompts: list[str] = []

    async def __aenter__(self) -> FakeAgent:
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    async def query(self, prompt: str) -> None:
        self.prompts.append(prompt)

    async def receive_response(self) -> AsyncIterator[Any]:
        yield AssistantMessage(content=[BASH], model="m")
        assert self.options.can_use_tool is not None
        verdict = await self.options.can_use_tool(
            "Bash", BASH.input, ToolPermissionContext(tool_use_id="t1")
        )
        FakeAgent.verdicts.append(verdict)
        yield UserMessage(
            content=[ToolResultBlock(tool_use_id="t1", content="Everything up-to-date")]
        )
        yield AssistantMessage(content=[TextBlock("Pushed.")], model="m")
        yield ResultMessage(
            subtype="success",
            duration_ms=1,
            duration_api_ms=1,
            is_error=False,
            num_turns=2,
            session_id="s",
        )


def ctx() -> Context:
    return Context(config=config.load())


def test_a_run_puts_the_session_on_the_card_and_takes_permission_from_the_board(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def board(frame: dict[str, Any]) -> dict[str, Any] | None:
        if frame["type"] == "prompt":
            return {
                "type": "answer",
                "prompt_id": frame["prompt"]["id"],
                "status": "answered",
                "key": "allow_once",
                "answer": "Allow once",
            }
        return None

    socket = FakeSocket(board)

    async def connect(base_url: str, token: str, session_id: str) -> FakeSocket:
        return socket

    FakeAgent.verdicts = []
    monkeypatch.setattr(runner, "_connect", connect)
    monkeypatch.setattr(runner, "ClaudeSDKClient", FakeAgent)

    asyncio.run(runner.run_agent(ctx(), fake_api.TASK_ONE, "Push it", interactive=False))

    assert FakeAgent.verdicts == [PermissionResultAllow()]
    kinds = [f["type"] for f in frames(socket.sent)]
    assert kinds[-1] == "bye"
    assert "prompt" in kinds and "feed" in kinds
    assert levels(socket.sent)[-1] == ("waiting", "turn_ended", None)
    assert ("waiting", "permission", "Running git push origin staging") in levels(socket.sent)
    assert "Pushed." in capsys.readouterr().out


def test_a_refused_session_never_starts_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    def board(frame: dict[str, Any]) -> dict[str, Any] | None:
        return {"type": "error", "code": "not_found", "message": "No task ATL-1."}

    async def connect(base_url: str, token: str, session_id: str) -> FakeSocket:
        return FakeSocket(board)

    def no_agent(options: ClaudeAgentOptions) -> None:
        raise AssertionError("the agent started")

    monkeypatch.setattr(runner, "_connect", connect)
    monkeypatch.setattr(runner, "ClaudeSDKClient", no_agent)

    with pytest.raises(CylistError, match="No task ATL-1"):
        asyncio.run(runner.run_agent(ctx(), fake_api.TASK_ONE, "Push it", interactive=False))


# --- The command -----------------------------------------------------------------


def test_run_refuses_an_id(run: Runner) -> None:
    assert run("run", fake_api.TASK_ONE_ID).code == 1


def test_run_without_the_sdk_says_how_to_get_it(
    run: Runner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "cylist_cli.runner", None)

    result = run("run", "ATL-1")

    assert result.code == 1
    assert "cylist-cli[agent]" in result.err
