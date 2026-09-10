"""What an agent and the server say to each other over a socket.

**This module is the contract.** WebSocket routes do not appear in
``openapi.json``, so ``npm run api:types`` generates nothing for them and the
usual "the schema is the contract" rule does not reach here. The definitions
below are authoritative; the CLI's ``presence/protocol.py`` and the browser's
``api/realtime.ts`` mirror them by hand, and the three change together.

The agent's side, in the order a session sends them:

.. code-block:: json

    {"type": "state", "task": "ATL-41", "state": "working",
     "reason": null, "client_name": "ATL-41"}
    {"type": "heartbeat"}
    {"type": "bye", "reason": "session_ended"}

``state`` carries exactly the three fields of :class:`AgentSessionPut`, and
carries them *as* one, so a report cannot mean one thing over HTTP and
another over a socket. The task is in the message rather than in the path
because a session walks the board: it binds to another card by naming it
here, and the server ends the row it left with ``moved``, which is what the
PUT route has always done.

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

from app.models.agent_session import AgentSessionReason
from app.schemas.agent_sessions import AgentSessionPut, AgentSessionRead
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


class HeartbeatMessage(Schema):
    """Nothing to report, still here. See the module note on the idle window."""

    type: Literal["heartbeat"]


class ByeMessage(Schema):
    """A session ending on purpose."""

    type: Literal["bye"]
    reason: AgentSessionReason | None = Field(
        default=None, description="Defaults to `session_ended`."
    )


AgentMessage = Annotated[
    StateMessage | HeartbeatMessage | ByeMessage,
    Field(discriminator="type"),
]

_incoming: TypeAdapter[StateMessage | HeartbeatMessage | ByeMessage] = TypeAdapter(AgentMessage)


def parse(raw: str) -> StateMessage | HeartbeatMessage | ByeMessage | None:
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


def error(code: str, message: str) -> dict[str, object]:
    """Why the socket is about to close, in words the client can read."""
    return {"type": "error", "code": code, "message": message}
