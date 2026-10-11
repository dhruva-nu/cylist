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

from pydantic import Field, TypeAdapter, ValidationError

from app.core.clock import now
from app.models.agent_session import AgentSessionReason
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


class HeartbeatMessage(Schema):
    """Nothing to report, still here. See the module note on the idle window."""

    type: Literal["heartbeat"]


class ByeMessage(Schema):
    """A session ending on purpose."""

    type: Literal["bye"]
    reason: AgentSessionReason | None = Field(
        default=None, description="Defaults to `session_ended`."
    )


Incoming = StateMessage | FeedMessage | HeartbeatMessage | ByeMessage

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


FEED_CHANGED = "agent.feed"
"""The event type a new feed line is published as, and the doorbell it rings."""


def board_changed() -> dict[str, object]:
    """The board socket's doorbell: something on this board committed."""
    return {"type": "board.changed", "at": now().isoformat()}


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
