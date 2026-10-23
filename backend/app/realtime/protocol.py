"""What an agent and the server say to each other over a socket.

**This module is the contract.** WebSocket routes do not appear in
``openapi.json``, so ``npm run api:types`` generates nothing for them and the
usual "the schema is the contract" rule does not reach here. The definitions
below are authoritative; the CLI's ``presence/protocol.py`` and the browser's
``api/realtime.ts`` mirror them by hand, and the three change together.

The agent's side, in the order a session sends them:

.. code-block:: json

    {"type": "state", "task": "ATL-41",
     "report": {"state": "working", "reason": null, "client_name": "ATL-41",
                "note": "Running pytest test_pr_states.py"}}
    {"type": "feed", "task": "ATL-41",
     "entries": [{"at": "2026-10-12T14:31:02Z", "kind": "run",
                  "tool": "Bash", "text": "pytest test_pr_states.py"}]}
    {"type": "heartbeat"}
    {"type": "bye", "reason": "session_ended"}

``state`` carries exactly the fields of :class:`AgentSessionPut`, and
carries them *as* one, so a report cannot mean one thing over HTTP and
another over a socket. Its ``note`` is the session's current action, sent
on every tool call; a report without one clears it.

The task is in the message rather than in the path because a session walks
the board: it binds to another card by naming it here, and the server ends
the row it left with ``moved``, which is what the PUT route has always done.

``feed`` is what the session read, edited, ran and said since the last one —
CYLIST-88. The daemon follows the session's transcript and sends the lines it
finds, already redacted on the agent's machine; the server keeps them against
the session's open row on that card and rings an ``agent.feed`` doorbell for
whoever has the card open. It is not acknowledged: a line is not worth a
round trip, and a frame for a card the session is not on is dropped rather
than refused, because a feed is never a reason to take a session off the
board.

``prompt`` and ``withdraw`` are a permission (CYLIST-89). Claude Code's
``PermissionRequest`` hook hands the daemon what a tool wants to do, and the
daemon puts it on the card with a ``prompt`` frame whose ``id`` it drew
itself, after a level saying the session is waiting for permission. When a
person answers on the board, the server sends **down this socket** — the one
thing it sends that is not a reply::

    {"type": "answer", "prompt_id": "…", "status": "answered",
     "key": "allow_session", "answer": "Allow for this session"}

``status`` is ``expired`` or ``withdrawn`` when it closed without an answer
here; the hook then leaves the terminal's own dialog to it. The daemon sends
``withdraw`` when the terminal won the race — the tool ran (its
``PostToolUse``), or the hook went away — or its own wait ran out
(``expired``). Claude Code does not end the hook when its dialog is
answered, so a terminal "no" is noticed only by the session's next report,
which withdraws any open permission on the server's side. A ``prompt`` re-sent after a
reconnect is answered with its outcome if it has one, which is how an answer
given while the link was down still arrives. A question asked with the MCP
``ask_human`` tool never touches this socket: its tool long-polls over HTTP.

``heartbeat`` is the client keeping its own socket alive. It exists because
the server's idle window is measured on application messages — protocol
pongs are answered below the ASGI layer and never reach a handler — and
because the *client* is the only one that knows the difference between "busy
for twenty minutes on one tool call" and "waiting for a human who has gone
home". It sends them while working and stops while waiting, so the window
closing means the agent is gone or the person is, and the board draws those
the same way.

``bye`` is the only ending that is not a loss. Everything else — a drop, an
idle timeout, a process killed — arrives as silence and is recorded as
``connection_lost``.
"""

from __future__ import annotations

from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, TypeAdapter, ValidationError

from app.core.clock import now
from app.models.agent_session import AgentPromptStatus, AgentSessionReason
from app.schemas.agent_sessions import AgentFeedEntryIn, AgentSessionPut, AgentSessionRead
from app.schemas.common import Schema

MAX_FRAME_BYTES = 64 * 1024
"""Longest message this endpoint will look at.

uvicorn's own ceiling is 16 MiB, which is how much an unauthenticated peer on
a public funnel could make the process buffer before anyone had checked who
they were. Nothing here is remotely that big.
"""


class StateMessage(Schema):
    """A session saying what it is doing, and on which card."""

    type: Literal["state"]
    task: str = Field(max_length=200, description="Reference such as 'ATL-41', or an id.")
    report: AgentSessionPut = Field(
        description="Exactly what the HTTP route takes — one shape, two transports."
    )


FEED_MAX_ENTRIES = 50
"""Most lines one ``feed`` frame may carry. The daemon sends fewer; the
ceiling keeps one frame inside :data:`MAX_FRAME_BYTES` whatever it sends."""


class FeedMessage(Schema):
    """What a session did since its last ``feed``, line by line."""

    type: Literal["feed"]
    task: str = Field(max_length=200, description="The card the session is on.")
    entries: list[AgentFeedEntryIn] = Field(max_length=FEED_MAX_ENTRIES)


PROMPT_TEXT_MAX = 1000
"""Longest permission text a frame may carry. The CLI clips far below this."""


class PermissionPrompt(Schema):
    """A permission Claude Code wants, as the session's daemon puts it on the card."""

    id: UUID = Field(
        description="Drawn by the daemon, so a frame re-sent after a reconnect is the same "
        "prompt rather than a second one."
    )
    kind: Literal["permission"]
    text: str = Field(min_length=1, max_length=PROMPT_TEXT_MAX)
    tool: str | None = Field(default=None, max_length=200)


class PromptMessage(Schema):
    """The harness is asking a person something; put it on the card."""

    type: Literal["prompt"]
    task: str = Field(max_length=200, description="The card the session is on.")
    prompt: PermissionPrompt


class WithdrawMessage(Schema):
    """The session stopped waiting on a prompt before anybody here answered."""

    type: Literal["withdraw"]
    task: str = Field(max_length=200)
    prompt_id: UUID
    reason: Literal["answered_in_terminal", "interrupted", "expired"]


class HeartbeatMessage(Schema):
    """Nothing to report, still here. See the module note on the idle window."""

    type: Literal["heartbeat"]


class ByeMessage(Schema):
    """A session ending on purpose."""

    type: Literal["bye"]
    reason: AgentSessionReason | None = Field(
        default=None, description="Defaults to `session_ended`."
    )


Incoming = (
    StateMessage | FeedMessage | PromptMessage | WithdrawMessage | HeartbeatMessage | ByeMessage
)

AgentMessage = Annotated[Incoming, Field(discriminator="type")]

_incoming: TypeAdapter[Incoming] = TypeAdapter(AgentMessage)


def parse(raw: str) -> Incoming | None:
    """Read one frame, or ``None`` if it is not one we understand.

    Returning ``None`` rather than raising because the caller's answer to
    both a malformed frame and an unknown one is the same close code, and
    neither is exceptional enough to unwind a handler for.
    """
    if len(raw.encode()) > MAX_FRAME_BYTES:
        return None
    try:
        return _incoming.validate_json(raw)
    except ValidationError:
        return None


# --- What the server says --------------------------------------------------


def ready(client_session_id: str, actor_label: str) -> dict[str, object]:
    """The socket is good. Sent once, before anything else."""
    return {
        "type": "ready",
        "client_session_id": client_session_id,
        "actor_label": actor_label,
    }


def ack(session: AgentSessionRead) -> dict[str, object]:
    """A report was applied, with the row as it now stands.

    The same body the PUT returns, so a client can keep its own view of the
    session without a second request.
    """
    return {"type": "ack", "session": session.model_dump(mode="json")}


def answer(
    prompt_id: UUID,
    status: AgentPromptStatus,
    *,
    key: str | None = None,
    answer: str | None = None,
) -> dict[str, object]:
    """A permission prompt closed: what the agent's daemon hands back to the hook.

    ``status`` ``answered`` carries the option's ``key`` — ``allow_once``,
    ``allow_session`` or ``deny`` — and its label as ``answer``. Anything else
    means nobody here answered, and the hook leaves the terminal to it.
    """
    return {
        "type": "answer",
        "prompt_id": str(prompt_id),
        "status": status.value,
        "key": key,
        "answer": answer,
    }


PROMPT_CHANGED = "agent.prompt"
"""The event a prompt opening or closing is published as. Rings the board's
doorbell, wakes long-polls, and carries a permission's answer to its agent —
see :mod:`app.realtime.hub`. Never sent down a socket under this name."""

FEED_CHANGED = "agent.feed"
"""The event type a new feed line is published as, and the doorbell it rings."""

AGENT_CONNECTED = "agent.connected"
"""The event a new agent socket is published as, so that whichever process
holds an older socket for the same session lets go of it — see
:meth:`app.realtime.hub.Hub.register_agent`. Never sent down a socket."""


CHANGED_MAX = 25
"""The most things a ``board.changed`` names before it stops naming them. A
burst past this is a bulk change, and the honest hint for one is "anything"."""


def board_changed(changed: list[dict[str, str]] | None = None) -> dict[str, object]:
    """The board socket's doorbell: something on this board committed.

    ``changed`` says *what*, as ``{"entity_type", "entity_id"}`` references
    and nothing more — no field, no value — so a browser can refetch the one
    list that is out of date instead of all of them, while the doorbell stays
    a doorbell: the data still comes from the HTTP read that builds it. ``None``
    means "something, and no word on what", which is also what a burst bigger
    than :data:`CHANGED_MAX` becomes; a browser must then read everything.
    """
    return {"type": "board.changed", "at": now().isoformat(), "changed": changed}


def merge_changed(first: dict[str, object], second: dict[str, object]) -> dict[str, object]:
    """One ``board.changed`` standing for two, for a watcher that has not read the first.

    Their hints are joined, without repeats; if either says "anything", so
    does the result, and so does one that has grown past :data:`CHANGED_MAX`.
    """
    left, right = first.get("changed"), second.get("changed")
    if not isinstance(left, list) or not isinstance(right, list):
        return {**first, "changed": None}
    joined = list(left)
    for ref in right:
        if ref not in joined:
            joined.append(ref)
    return {**first, "changed": joined if len(joined) <= CHANGED_MAX else None}


def changed_ref(event: dict[str, object]) -> list[dict[str, str]] | None:
    """The hint a published event gives, or ``None`` where it names nothing."""
    entity_type, entity_id = event.get("entity_type"), event.get("entity_id")
    if not isinstance(entity_type, str):
        return None
    return [{"entity_type": entity_type, "entity_id": str(entity_id) if entity_id else ""}]


def feed_changed(task_id: str) -> dict[str, object]:
    """The board socket's doorbell for a card's feed: refetch it if it is open.

    Sent down *board* sockets, not agent ones. Carries the card and nothing
    else, for the reason every board message is a doorbell — see
    :mod:`app.realtime.hub`.
    """
    return {"type": FEED_CHANGED, "task_id": task_id, "at": now().isoformat()}


def error(code: str, message: str) -> dict[str, object]:
    """Why the socket is about to close, in words the client can read."""
    return {"type": "error", "code": code, "message": message}
