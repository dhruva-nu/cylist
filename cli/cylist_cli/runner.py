"""``cylist run ATL-41``: an agent Cylist starts itself, through the Claude Agent SDK.

``cylist work`` hands the terminal to ``claude`` and watches it from outside:
hooks say what it is doing, a daemon holds the socket, the transcript is
tailed for the feed, and a permission is a race between the board and the
terminal's own dialog. Here the agent runs inside this process instead, and
the SDK hands over directly what those had to piece together:

* every message as it happens, which becomes the card's Now line and its
  feed through the same :mod:`.presence.narrate` and
  :class:`.presence.transcript.Narrator` the daemon uses, so a line reads the
  same whichever way it came;
* ``can_use_tool``, so a permission waits on the board and the board's answer
  is the decision. There is no terminal dialog to race;
* an ``ask_human`` tool that runs in this process, and so says which session
  is asking instead of the server guessing it from the person.

It speaks the agent socket exactly as the daemon does
(``backend/app/realtime/protocol.py``), so the server cannot tell the two
apart and needs nothing new.

Imported only by ``cylist run``'s handler: the SDK is an optional extra, and
``commands`` imports every command module on every hook invocation.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
import logging
import threading
import uuid
import warnings
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from claude_agent_sdk import (
    AssistantMessage,
    CanUseToolShadowedWarning,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    McpSdkServerConfig,
    Message,
    PermissionResult,
    PermissionResultAllow,
    PermissionResultDeny,
    ResultMessage,
    TextBlock,
    ToolPermissionContext,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
    create_sdk_mcp_server,
    tool,
)
from claude_agent_sdk.types import PermissionMode, PermissionRuleValue, PermissionUpdate

from cylist_cli import endpoints, output
from cylist_cli.client import Client
from cylist_cli.context import Context
from cylist_cli.errors import CylistError
from cylist_cli.presence import narrate, protocol
from cylist_cli.presence.machine import KEEPALIVE_EVERY, backoff
from cylist_cli.presence.transcript import FeedEntry, Narrator

logger = logging.getLogger(__name__)

PERMISSION_WAIT = 580.0
"""Seconds a permission waits on the board before it is denied. Inside the
server's own 590, as the daemon's is, so the prompt is withdrawn here rather
than expired there."""

ASK_POLL_SECONDS = 20
"""Seconds per long-poll of a question, as the MCP ``ask_human`` uses."""

CONNECT_TIMEOUT = 10.0
FIRST_LINK_WAIT = 20.0
"""Seconds to get onto the card before giving up. The agent does not start
until it is: a question asked before the session has a row would be refused."""

REFUSALS = frozenset({"unauthorized", "forbidden", "not_found"})
"""Error frames that no reconnect will cure."""

ASK_TOOL = "mcp__board__ask_human"


# --- What the agent did, as frames -------------------------------------------


@dataclass
class _Asking:
    text: str
    tool: str | None
    answer: asyncio.Future[dict[str, Any]]


class Bridge:
    """The agent's messages in, frames for its card out. No IO.

    Frames go to ``send`` in the order they are decided, and that order is the
    one thing to be careful about. The server reads a ``working`` level as
    "the open permission was answered in the terminal" and takes it down. So
    while a permission waits, the level stays ``waiting`` even if a parallel
    tool call starts, and each decision and its frame are made in one
    synchronous step with no ``await`` between them.
    """

    def __init__(self, task: str, send: Callable[[str], None], *, cwd: str = "") -> None:
        self.task = task
        self._send = send
        self._cwd = cwd
        self._narrator = Narrator()
        self._note: str | None = None
        self._turn_ended = False
        self._asking: OrderedDict[str, _Asking] = OrderedDict()

    def level(self) -> str:
        """Where the session is, as the frame the server keeps."""
        if self._asking:
            latest = next(reversed(self._asking.values()))
            return protocol.hello_frame(self.task, self.task, "waiting", "permission", latest.text)
        if self._turn_ended:
            return protocol.hello_frame(self.task, self.task, "waiting", "turn_ended", None)
        return protocol.hello_frame(self.task, self.task, "working", None, self._note)

    def resync(self) -> None:
        """All the server needs after a (re)connect: the level, then open permissions.

        A permission re-sent keeps its id, so the server treats it as the same
        prompt, and replies with its outcome if it was answered meanwhile.
        """
        self._send(self.level())
        for prompt_id, asking in self._asking.items():
            self._send(protocol.prompt_frame(self.task, prompt_id, asking.text, asking.tool))

    def turn(self, *, ended: bool) -> None:
        """A turn starting (a prompt was sent) or ending (the agent answered)."""
        self._turn_ended = ended
        self._note = None
        self._send(self.level())

    def saw(self, record: dict[str, Any]) -> list[FeedEntry]:
        """One agent message, in transcript shape. Returns its feed lines."""
        entries = self._narrator.read({**record, "cwd": self._cwd})
        calls = [
            block
            for block in (record.get("message") or {}).get("content") or []
            if isinstance(block, dict) and block.get("type") == "tool_use"
        ]
        if calls and record.get("type") == "assistant":
            last = calls[-1]
            self._note = narrate.describe_tool(str(last.get("name") or ""), last.get("input"))
            if not self._asking:
                self._send(self.level())
        if entries:
            for frame in protocol.feed_frames(self.task, [entry.wire() for entry in entries]):
                self._send(frame)
        return entries

    async def ask(self, tool_name: str, tool_input: Any, *, wait: float) -> dict[str, Any] | None:
        """Put a permission on the card and wait for a person to answer it.

        Returns the ``answer`` frame, or ``None`` when nobody answered here:
        it ran out of time or the server closed it without an answer.
        """
        prompt_id = str(uuid.uuid4())
        text = narrate.describe_tool(tool_name, tool_input)
        answer: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._asking[prompt_id] = _Asking(text, tool_name or None, answer)
        self._send(self.level())
        self._send(protocol.prompt_frame(self.task, prompt_id, text, tool_name or None))
        try:
            heard = await asyncio.wait_for(answer, wait)
        except TimeoutError:
            self._send(protocol.withdraw_frame(self.task, prompt_id, "expired"))
            return None
        except asyncio.CancelledError:
            self._send(protocol.withdraw_frame(self.task, prompt_id, "interrupted"))
            raise
        finally:
            del self._asking[prompt_id]
            self._send(self.level())
        return heard if heard.get("status") == "answered" else None

    def heard(self, frame: dict[str, Any]) -> None:
        """The server closed a permission: wake whoever is waiting on it."""
        asking = self._asking.get(str(frame.get("prompt_id")))
        if asking is not None and not asking.answer.done():
            asking.answer.set_result(frame)


def decision(
    answer: dict[str, Any] | None,
    tool_name: str,
    tool_input: Any,
    suggestions: list[PermissionUpdate],
) -> PermissionResult:
    """The SDK's verdict for what the board said.

    Unlike the hook, there is no terminal to fall back on, so no answer is a
    denial. "Allow for this session" adds allow rules for this session only,
    with the same narrowing as the hook's ``_session_permissions``: the
    harness's suggested rules, moved to ``session``, or else the tool (for a
    shell command, that exact command). Mode switches it suggests are never
    taken.
    """
    key = answer.get("key") if answer else None
    if key == "allow_once":
        return PermissionResultAllow()
    if key == "allow_session":
        rules = [
            PermissionUpdate(
                type="addRules", rules=entry.rules, behavior="allow", destination="session"
            )
            for entry in suggestions
            if entry.type == "addRules" and entry.behavior == "allow" and entry.rules
        ]
        if not rules:
            command = tool_input.get("command") if isinstance(tool_input, dict) else None
            content = command.strip() if tool_name == "Bash" and isinstance(command, str) else None
            rules = [
                PermissionUpdate(
                    type="addRules",
                    rules=[PermissionRuleValue(tool_name=tool_name, rule_content=content or None)],
                    behavior="allow",
                    destination="session",
                )
            ]
        return PermissionResultAllow(updated_permissions=rules)
    if key == "deny":
        return PermissionResultDeny(message="Denied from the Cylist board.")
    return PermissionResultDeny(
        message="Nobody allowed this on the Cylist board in time, so it did not run."
    )


def record_of(message: Message) -> dict[str, Any] | None:
    """An SDK message in the shape of a transcript record, for :class:`Narrator`.

    ``None`` for anything that is not the agent's own conversation: a
    subagent's (it has a parent tool call), a person's prompt (plain text),
    system and result messages.
    """
    if isinstance(message, AssistantMessage):
        kind = "assistant"
    elif isinstance(message, UserMessage) and isinstance(message.content, list):
        kind = "user"
    else:
        return None
    if message.parent_tool_use_id:
        return None
    blocks: list[dict[str, Any]] = []
    for block in message.content:
        if isinstance(block, TextBlock):
            blocks.append({"type": "text", "text": block.text})
        elif isinstance(block, ToolUseBlock):
            blocks.append(
                {"type": "tool_use", "id": block.id, "name": block.name, "input": block.input}
            )
        elif isinstance(block, ToolResultBlock):
            blocks.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.tool_use_id,
                    "content": block.content,
                    "is_error": block.is_error,
                }
            )
    return {
        "type": kind,
        "timestamp": datetime.now(UTC).isoformat(),
        "message": {"content": blocks},
    }


# --- The socket ----------------------------------------------------------------


class Link:
    """The agent socket, kept up for as long as the agent runs.

    One connection at a time. Every (re)connect starts with the bridge's
    resync; frames queued while the link was down are dropped, since the
    resync already says where the session is now. Feed lines from that gap
    are lost, which costs a few lines of history and nothing else.
    """

    def __init__(self, ctx: Context, session_id: str, connect: Any = None) -> None:
        self._ctx = ctx
        self._session_id = session_id
        self._connect = connect or _connect
        self._outbox: asyncio.Queue[str] = asyncio.Queue()
        self._up = asyncio.Event()
        self._bye: str | None = None
        self._bye_sent = asyncio.Event()
        self.refused: str | None = None

    def send(self, frame: str) -> None:
        self._outbox.put_nowait(frame)

    async def ready(self, task: asyncio.Task[None]) -> None:
        """Wait until the server has acknowledged the session on its card."""
        waiting = asyncio.ensure_future(self._up.wait())
        done, _ = await asyncio.wait(
            {waiting, task}, timeout=FIRST_LINK_WAIT, return_when="FIRST_COMPLETED"
        )
        waiting.cancel()
        if self.refused:
            raise CylistError(f"Cylist refused the session: {self.refused}")
        if task in done:
            task.result()
        if not self._up.is_set():
            raise CylistError("Could not reach Cylist to put the session on its card.")

    async def finish(self, reason: str) -> None:
        """Say goodbye, so the card shows the session ended rather than lost."""
        self._bye = reason
        self.send(protocol.bye_frame(reason))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._bye_sent.wait(), 5)

    async def run(self, bridge: Bridge) -> None:
        addresses = endpoints.order(self._ctx.config.urls)
        attempt = 0
        while self.refused is None and not self._bye_sent.is_set():
            base_url = addresses[attempt % len(addresses)]
            try:
                socket = await self._connect(
                    base_url, self._ctx.config.require_token(), self._session_id
                )
            except (OSError, TimeoutError, CylistError) as exc:
                logger.info("Connect to %s failed: %s", base_url, exc)
            else:
                endpoints.remember(base_url)
                attempt = 0
                async with socket:
                    while not self._outbox.empty():
                        self._outbox.get_nowait()
                    bridge.resync()
                    if self._bye is not None:
                        self.send(protocol.bye_frame(self._bye))
                    await self._pump(socket, bridge)
                if self.refused is not None or self._bye_sent.is_set():
                    return
            await asyncio.sleep(backoff(attempt))
            attempt += 1

    async def _pump(self, socket: Any, bridge: Bridge) -> None:
        reader = asyncio.ensure_future(self._read(socket, bridge))
        writer = asyncio.ensure_future(self._write(socket))
        try:
            await asyncio.wait({reader, writer}, return_when="FIRST_COMPLETED")
        finally:
            reader.cancel()
            writer.cancel()
        for task in (reader, writer):
            if task.done() and not task.cancelled() and task.exception() is not None:
                logger.info("Link dropped: %s", task.exception())

    async def _write(self, socket: Any) -> None:
        while True:
            try:
                frame = await asyncio.wait_for(self._outbox.get(), KEEPALIVE_EVERY)
            except TimeoutError:
                frame = protocol.heartbeat_frame()
            await socket.send(frame)
            if json.loads(frame).get("type") == "bye":
                self._bye_sent.set()
                return

    async def _read(self, socket: Any, bridge: Bridge) -> None:
        async for raw in socket:
            frame = protocol.parse_server_frame(raw)
            if frame is None:
                continue
            kind = frame.get("type")
            if kind == "ack":
                self._up.set()
            elif kind == "answer":
                bridge.heard(frame)
            elif kind == "error":
                logger.warning("Cylist said: %s", frame.get("message"))
                if frame.get("code") in REFUSALS:
                    self.refused = str(frame.get("message") or frame.get("code"))
                    return


async def _connect(base_url: str, token: str, session_id: str) -> Any:
    from websockets.asyncio.client import connect

    return await connect(
        protocol.ws_url(base_url, session_id),
        additional_headers={"Authorization": f"Bearer {token}"},
        user_agent_header="cylist-cli",
        open_timeout=CONNECT_TIMEOUT,
        close_timeout=2,
    )


# --- ask_human -----------------------------------------------------------------

ASK_DESCRIPTION = (
    "Ask the person you are working for a question, on your Cylist card, and wait "
    "for the answer. Use it when you cannot reasonably decide alone (a product "
    "choice, an ambiguity in the card, something only they know), not to report "
    "progress. Offer 2-4 short options when the choice is between known "
    "alternatives; they may also type a reply. The answer is this tool's result, so "
    "you carry on in the same turn. If nobody answers within the timeout the result "
    "says so: decide yourself and say what you chose."
)

ASK_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "question": {"type": "string", "description": "One clear question."},
        "options": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Up to six short choices, in the order to show them.",
        },
        "timeout_minutes": {
            "type": "integer",
            "minimum": 1,
            "maximum": 240,
            "description": "How long to wait for an answer. Default 30.",
        },
    },
    "required": ["question"],
}


async def ask_question(
    http: Client, task: str, session_id: str, question: str, options: list[str], minutes: int
) -> dict[str, Any]:
    """Put a question on the card for this session, and long-poll for the answer."""
    asked = await _in_thread(
        http.post,
        f"/tasks/{task}/agent-prompts",
        {
            "question": question,
            "options": options,
            "timeout_seconds": max(60, minutes * 60),
            "client_session_id": session_id,
        },
    )
    output.warn(f"  Asked on the board: {question}")
    path = f"/tasks/{task}/agent-prompts/{asked['id']}"
    current: dict[str, Any] = asked
    try:
        while current.get("status") == "open":
            current = await _in_thread(http.get, path, wait=ASK_POLL_SECONDS)
    except asyncio.CancelledError:
        with contextlib.suppress(Exception):
            await asyncio.wait_for(
                _in_thread(http.post, f"{path}/withdraw", {"reason": "interrupted"}), 5
            )
        raise
    return answer_payload(current, minutes)


def answer_payload(prompt: dict[str, Any], minutes: int) -> dict[str, Any]:
    """What the model is handed back. The same shape as the MCP ``ask_human``'s."""
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
            f"Nobody answered within {minutes} minutes. Decide for yourself and say what you chose."
        )
    else:
        reason = str(prompt.get("closed_reason") or "the session ended").replace("_", " ")
        why = f"The question was withdrawn before anybody answered it: {reason}."
    return {"answered": False, "status": status, "message": why}


def board_server(http: Client, task: str, session_id: str) -> McpSdkServerConfig:
    @tool("ask_human", ASK_DESCRIPTION, ASK_SCHEMA)
    async def ask_human(args: dict[str, Any]) -> dict[str, Any]:
        options = [str(option) for option in args.get("options") or []]
        minutes = min(240, max(1, int(args.get("timeout_minutes") or 30)))
        try:
            payload = await ask_question(
                http, task, session_id, str(args.get("question") or ""), options, minutes
            )
        except CylistError as exc:
            return {"content": [{"type": "text", "text": exc.message}], "is_error": True}
        return {"content": [{"type": "text", "text": json.dumps(payload, indent=2)}]}

    return create_sdk_mcp_server("board", tools=[ask_human])


# --- Running it ----------------------------------------------------------------


def briefing(task: dict[str, Any]) -> str:
    """The first prompt, when none is given: the card itself."""
    ref = task.get("reference")
    description = str(task.get("description") or "").strip()
    return f"Work on Cylist card {ref}: {task.get('title', '')}\n\n{description}".strip()


def system_note(ref: str) -> str:
    return (
        f"You are working on Cylist card {ref}. What you do shows on the card as you "
        "work, and the person you work for watches the board rather than this terminal. "
        f"When you need a decision from them, use the {ASK_TOOL} tool; the answer comes "
        "back as its result. Tool permissions are also answered on the board."
    )


async def run_agent(
    ctx: Context,
    task: dict[str, Any],
    prompt: str,
    *,
    model: str | None = None,
    permission_mode: PermissionMode | None = None,
    interactive: bool = True,
) -> None:
    """Put a session on the card, run the agent until it is done, take it off."""
    ref = str(task["reference"])
    session_id = str(uuid.uuid4())
    link = Link(ctx, session_id)
    bridge = Bridge(ref, link.send, cwd=str(Path.cwd()))
    linking = asyncio.ensure_future(link.run(bridge))
    http = ctx.build_client(ctx.config.require_token())

    async def can_use_tool(
        tool_name: str, tool_input: dict[str, Any], context: ToolPermissionContext
    ) -> PermissionResult:
        output.warn(f"  Waiting on the board: {narrate.describe_tool(tool_name, tool_input)}")
        answer = await bridge.ask(tool_name, tool_input, wait=PERMISSION_WAIT)
        verdict = decision(answer, tool_name, tool_input, context.suggestions)
        output.warn(f"  Board: {answer.get('answer') if answer else 'no answer'}")
        return verdict

    options = ClaudeAgentOptions(
        session_id=session_id,
        cwd=str(Path.cwd()),
        model=model,
        permission_mode=permission_mode,
        system_prompt={"type": "preset", "preset": "claude_code", "append": system_note(ref)},
        mcp_servers={"board": board_server(http, ref, session_id)},
        allowed_tools=[ASK_TOOL],
        can_use_tool=can_use_tool,
        # The user-wide cylist hooks fire in this claude too. Unbound and with
        # presence off they do nothing, so the card hears one session, not two.
        env={"CYLIST_TASK": "", "CYLIST_PRESENCE": "off"},
    )
    # ask_human is allowed outright on purpose, so the callback never sees it;
    # the SDK's warning that the allow rule "shadows" can_use_tool is about
    # exactly that and nothing else.
    warnings.filterwarnings("ignore", category=CanUseToolShadowedWarning)
    try:
        await link.ready(linking)
        output.warn(f"{ref} is on the board. Session {session_id}")
        async with ClaudeSDKClient(options=options) as agent:
            while prompt:
                bridge.turn(ended=False)
                await agent.query(prompt)
                async for message in agent.receive_response():
                    _show(message, bridge)
                bridge.turn(ended=True)
                prompt = await _next_prompt() if interactive else ""
    finally:
        with contextlib.suppress(Exception):
            await link.finish("session_ended")
        linking.cancel()
        http.close()


def _show(message: Message, bridge: Bridge) -> None:
    record = record_of(message)
    for entry in bridge.saw(record) if record else []:
        if entry.kind == "said":
            output.echo(entry.text)
        else:
            output.warn(f"  {entry.kind.upper():<6} {entry.text}")
    if isinstance(message, ResultMessage):
        cost = f", ${message.total_cost_usd:.2f}" if message.total_cost_usd else ""
        output.warn(f"  Turn over: {message.num_turns} steps{cost}")


async def _next_prompt() -> str:
    try:
        line = await _in_thread(input, "\nReply to the agent, or press Enter to finish: ")
    except EOFError:
        return ""
    return line.strip()


def _in_thread[T](function: Callable[..., T], *args: Any, **kwargs: Any) -> Awaitable[T]:
    """Run a blocking call off the loop, in a daemon thread.

    Not ``asyncio.to_thread``: its threads are joined when the loop closes, so
    Ctrl-C during a 20-second poll, or at the reply prompt, would hang until
    the call came back by itself.
    """
    loop = asyncio.get_running_loop()
    future: asyncio.Future[T] = loop.create_future()

    def settle(outcome: Callable[[], object]) -> None:
        if not future.done():
            outcome()

    def work() -> None:
        outcome: Callable[[], object]
        try:
            outcome = functools.partial(future.set_result, function(*args, **kwargs))
        except BaseException as exc:  # handed to the awaiting task, not swallowed
            outcome = functools.partial(future.set_exception, exc)
        # The loop is gone if the run ended while this call was out.
        with contextlib.suppress(RuntimeError):
            loop.call_soon_threadsafe(settle, outcome)

    threading.Thread(target=work, daemon=True).start()
    return future
