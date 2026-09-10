"""What the daemon does, as a function rather than as a process.

``step`` takes the machine and one event and returns the next machine and
what to do about it. No sockets, no threads, no clock — the time comes in as
an argument. That is what makes every rule below testable in microseconds
and without a network, which matters more here than anywhere else in the
CLI: the rules are about *durations*, and a suite that tested them by
waiting would either be slow or be lying.

The rules, in one place:

* A state the server already has is not sent again. Two ``Stop`` events in a
  row cost one frame; a ``Stop`` then a permission prompt cost two, because
  the reason differs.
* While the link is down the level is remembered and nothing is queued. On
  reconnect the *current* level is sent, not the history — see
  ``hello_frame``.
* The keepalive runs while working and not while waiting. This is the
  client's half of the idle contract: a tool call can run for twenty minutes
  without a hook event, and the server must not read that as absence; a human
  who has walked away must be read as exactly that, after five minutes.
* Nothing keeps a daemon alive forever. It exits when the session ends, when
  the server has been unreachable long enough that nobody is coming back, and
  when it has been waiting on a person for the idle window.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum

from cylist_cli.presence import protocol

IDLE_AFTER = 300.0
"""Seconds waiting on a human before the daemon gives the session up.

Matches ``AGENT_SOCKET_IDLE_AFTER`` on the server, which would close the
socket at the same moment anyway. Doing it here as well means the session
ends tidily, with a reason, instead of being timed out.
"""

KEEPALIVE_EVERY = 60.0
"""Seconds between heartbeats while working. A fifth of the idle window, so
four may be lost before anyone concludes anything."""

BACKOFF_GIVE_UP = 1800.0
"""Seconds of failing to connect before the daemon stops trying.

Half an hour of a backend that is not there means it is not coming back
inside this session. The next hook event starts a fresh daemon, so nothing
is lost but the process.
"""

_BACKOFF = (0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 30.0)


def backoff(attempt: int) -> float:
    """How long to wait before another attempt, flattening at thirty seconds."""
    return _BACKOFF[min(attempt, len(_BACKOFF) - 1)]


class Link(StrEnum):
    CONNECTING = "connecting"
    LIVE = "live"
    BACKOFF = "backoff"


class Agent(StrEnum):
    WORKING = "working"
    WAITING = "waiting"


# --- Events ----------------------------------------------------------------


@dataclass(frozen=True)
class HookState:
    """The hook says the session is working or waiting."""

    state: str
    reason: str | None
    client_name: str


@dataclass(frozen=True)
class HookBind:
    """The hook says the session has moved to another card."""

    task: str
    client_name: str


@dataclass(frozen=True)
class HookEnd:
    """The hook says the session is over."""

    reason: str


@dataclass(frozen=True)
class LinkUp:
    """The socket connected."""


@dataclass(frozen=True)
class LinkDown:
    """The socket went away."""


@dataclass(frozen=True)
class Tick:
    """Nothing happened; consider whether that is itself news."""


Event = HookState | HookBind | HookEnd | LinkUp | LinkDown | Tick


# --- Actions ---------------------------------------------------------------


@dataclass(frozen=True)
class Send:
    """Put a frame on the socket."""

    frame: str


@dataclass(frozen=True)
class Reconnect:
    """Try again after this many seconds."""

    after: float


@dataclass(frozen=True)
class Finish:
    """Stop. The reason is what the row is ended with, if it can be."""

    reason: str


Action = Send | Reconnect | Finish


# --- The machine -----------------------------------------------------------


@dataclass(frozen=True)
class Machine:
    """Everything the daemon knows, and nothing it owns."""

    session: str
    task: str
    client_name: str
    agent: Agent = Agent.WORKING
    reason: str | None = None
    link: Link = Link.CONNECTING
    attempt: int = 0
    since_hook: float = 0.0
    """Seconds since the hook last said anything."""
    since_link: float = 0.0
    """Seconds since the link last changed state."""
    since_keepalive: float = 0.0


def step(machine: Machine, event: Event, elapsed: float = 0.0) -> tuple[Machine, list[Action]]:
    """Advance the machine by one event, and say what to do.

    ``elapsed`` is the seconds since the last step, supplied by the caller so
    that the clock is an input rather than a dependency.
    """
    machine = replace(
        machine,
        since_hook=machine.since_hook + elapsed,
        since_link=machine.since_link + elapsed,
        since_keepalive=machine.since_keepalive + elapsed,
    )

    if isinstance(event, HookState):
        agent = Agent.WAITING if event.state == "waiting" else Agent.WORKING
        unchanged = agent is machine.agent and event.reason == machine.reason
        machine = replace(
            machine,
            agent=agent,
            reason=event.reason,
            client_name=event.client_name or machine.client_name,
            since_hook=0.0,
        )
        if unchanged or machine.link is not Link.LIVE:
            # Either the server already knows, or it cannot be told and will
            # be given the current level when the link comes back.
            return machine, []
        return machine, [Send(_level(machine))]

    if isinstance(event, HookBind):
        machine = replace(
            machine,
            task=event.task,
            client_name=event.client_name or machine.client_name,
            agent=Agent.WORKING,
            reason=None,
            since_hook=0.0,
        )
        if machine.link is not Link.LIVE:
            return machine, []
        return machine, [Send(_level(machine))]

    if isinstance(event, HookEnd):
        actions: list[Action] = []
        if machine.link is Link.LIVE:
            actions.append(Send(protocol.bye_frame(event.reason)))
        actions.append(Finish(event.reason))
        return machine, actions

    if isinstance(event, LinkUp):
        machine = replace(machine, link=Link.LIVE, attempt=0, since_link=0.0, since_keepalive=0.0)
        return machine, [Send(_level(machine))]

    if isinstance(event, LinkDown):
        delay = backoff(machine.attempt)
        machine = replace(machine, link=Link.BACKOFF, attempt=machine.attempt + 1, since_link=0.0)
        return machine, [Reconnect(delay)]

    # --- Tick ---------------------------------------------------------------
    if machine.agent is Agent.WAITING and machine.since_hook >= IDLE_AFTER:
        # Nobody is coming. The server's own window would close the socket at
        # the same moment; saying goodbye first makes it an ending rather than
        # a timeout.
        actions = []
        if machine.link is Link.LIVE:
            actions.append(Send(protocol.bye_frame("session_ended")))
        actions.append(Finish("idle"))
        return machine, actions

    if machine.link is Link.BACKOFF and machine.since_link >= BACKOFF_GIVE_UP:
        return machine, [Finish("backend_gone")]

    if (
        machine.link is Link.LIVE
        and machine.agent is Agent.WORKING
        and machine.since_keepalive >= KEEPALIVE_EVERY
    ):
        # Only while working. Silence while waiting is the signal, not a
        # failure — see the module note.
        machine = replace(machine, since_keepalive=0.0)
        return machine, [Send(protocol.heartbeat_frame())]

    return machine, []


def _level(machine: Machine) -> str:
    """The one frame that says where the session is now."""
    return protocol.hello_frame(
        machine.task, machine.client_name, machine.agent.value, machine.reason
    )
