"""The process that holds the socket.

Two threads and a queue. The main thread owns the WebSocket exclusively and
runs the reducer; a second thread accepts on the AF_UNIX socket and pushes
what the hooks say onto the queue. Nothing else is shared, so there is no
lock anywhere in here.

Synchronous, not asyncio. ``websockets.sync`` is a real client with real
ping/pong, and choosing it means the CLI gains no event loop, no
``pytest-asyncio``, and no colour on any existing function. The cost is one
thread, which a process whose entire job is to sit still can afford.

Every rule about *when* to do something lives in :mod:`.machine`, which is
pure. This module is the part that cannot be — sockets, threads, a clock —
and it is deliberately dull.
"""

from __future__ import annotations

import contextlib
import logging
import os
import queue
import signal
import threading
import time
from pathlib import Path
from typing import Any

from cylist_cli.context import Context
from cylist_cli.presence import ipc, protocol, supervisor
from cylist_cli.presence import machine as m

logger = logging.getLogger("cylist.presence")

CONNECT_TIMEOUT = 5.0
PING_INTERVAL = 20.0
PING_TIMEOUT = 20.0
"""Below any plausible proxy idle timeout, and together a ~40s budget for
noticing that a connection has died without saying so."""

TICK = 1.0
"""How often the reducer is given a chance to notice that time has passed."""


def log_path(session_id: str) -> Path:
    from cylist_cli.commands.hook import sessions_dir

    logs = sessions_dir().parent / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return logs / f"daemon-{session_id[:16]}.log"


def _clock() -> float:
    """Seconds that include time the machine spent asleep.

    ``monotonic`` stops while a laptop is suspended, so a lid closed for
    eight hours on a waiting session would come back believing no time had
    passed. ``BOOTTIME`` does not, which is what the idle window needs.
    """
    try:
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    except (AttributeError, OSError):  # pragma: no cover - not Linux
        return time.monotonic()


def run(ctx: Context, session_id: str, *, connect: Any = None) -> int:
    """Hold one session's socket until it ends. Always returns 0.

    ``connect`` is injectable so the loop can be driven in a test against a
    fake connection, with no network and no sleeping.
    """
    lock = supervisor.Singleton(session_id)
    if not lock.acquire():
        # Another daemon owns this session. Not an error: two hooks racing to
        # start one is the ordinary case.
        return 0

    _configure_logging(session_id)
    supervisor.write_pid(session_id)
    events: queue.Queue[m.Event] = queue.Queue()
    listener = ipc.Listener(session_id)
    state = _Shared(session_id)

    accepting = threading.Thread(
        target=listener.serve,
        args=(lambda message: _receive(message, state, events),),
        name="cylist-ipc",
        daemon=True,
    )
    accepting.start()

    def hang_up(*_: object) -> None:
        events.put(m.HookEnd("session_ended"))

    with contextlib.suppress(ValueError):  # not the main thread, in a test
        signal.signal(signal.SIGTERM, hang_up)
        signal.signal(signal.SIGINT, hang_up)

    try:
        _loop(ctx, session_id, events, state, connect=connect)
    except Exception:
        logger.exception("The presence daemon stopped on an error")
    finally:
        listener.close()
        supervisor.clear_pid(session_id)
        lock.release()
    return 0


class _Shared:
    """The little the IPC thread and the main thread both look at.

    Written by the main thread, read by the IPC thread, and only ever a
    couple of strings — so a plain object is enough and a lock would be
    ceremony. Nothing here decides anything; the reducer does that.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.task: str | None = None
        self.link: str = m.Link.CONNECTING.value


def _receive(message: dict[str, Any], state: _Shared, events: queue.Queue[m.Event]) -> str:
    """Turn one IPC message into an event, and answer the hook."""
    if message.get("session") != state.session_id:
        # A socket path collision, or a reused runtime directory. Cheap to
        # check, and the alternative is one conversation's state landing on
        # another's card.
        return protocol.refusal("wrong_session")

    kind = message.get("t")
    if kind == "state":
        events.put(
            m.HookState(
                state=str(message.get("state") or "working"),
                reason=message.get("reason"),
                client_name=str(message.get("client_name") or ""),
            )
        )
    elif kind == "bind":
        events.put(
            m.HookBind(
                task=str(message.get("task") or ""),
                client_name=str(message.get("client_name") or ""),
            )
        )
    elif kind == "end":
        events.put(m.HookEnd(str(message.get("reason") or "session_ended")))
    # "ping" asks for the ack and nothing else.
    return protocol.ack(state.link, state.task, os.getpid())


def _loop(
    ctx: Context,
    session_id: str,
    events: queue.Queue[m.Event],
    state: _Shared,
    *,
    connect: Any = None,
) -> None:
    """Connect, pump events through the reducer, and do what it says."""
    connect = connect or _connect
    token = ctx.config.token
    if not token:
        logger.info("No token configured; nothing to report with.")
        return

    # The first event is always the one that started us, so the machine can
    # be built before anything is read.
    first = events.get()
    if isinstance(first, m.HookEnd):
        return
    machine = _initial(session_id, first)
    state.task = machine.task

    socket: Any = None
    last = _clock()
    retry_at = 0.0

    while True:
        if socket is None and _clock() >= retry_at:
            try:
                # Re-read the token every attempt, so a fresh `cylist login`
                # heals a daemon that is already running.
                socket = connect(ctx.config.url, ctx.config.token or token, session_id)
            except Exception as exc:
                logger.info("Connect failed: %s", exc)
                machine, actions = _advance(machine, m.LinkDown(), last)
                last = _clock()
                retry_at = last + _delay(actions)
                state.link = machine.link.value
                if _finished(actions):
                    return
                continue
            machine, actions = _advance(machine, m.LinkUp(), last)
            last = _clock()
            state.link = machine.link.value
            if not _perform(socket, actions):
                _drop(socket)
                socket = None
                continue

        try:
            event: m.Event = events.get(timeout=TICK)
        except queue.Empty:
            event = m.Tick()

        machine, actions = _advance(machine, event, last)
        last = _clock()
        state.task = machine.task
        state.link = machine.link.value

        if socket is not None and not _perform(socket, actions):
            _drop(socket)
            socket = None
            machine, more = _advance(machine, m.LinkDown(), last)
            last = _clock()
            retry_at = last + _delay(more)
            state.link = machine.link.value
            if _finished(more):
                return
            continue

        if _finished(actions):
            _close(socket)
            return

        if socket is not None and _dropped(socket):
            _drop(socket)
            socket = None
            machine, more = _advance(machine, m.LinkDown(), last)
            last = _clock()
            retry_at = last + _delay(more)
            state.link = machine.link.value
            if _finished(more):
                return


def _initial(session_id: str, first: m.Event) -> m.Machine:
    if isinstance(first, m.HookBind):
        return m.Machine(session=session_id, task=first.task, client_name=first.client_name)
    if isinstance(first, m.HookState):
        return m.Machine(
            session=session_id,
            task="",
            client_name=first.client_name,
            agent=m.Agent.WAITING if first.state == "waiting" else m.Agent.WORKING,
            reason=first.reason,
        )
    return m.Machine(session=session_id, task="", client_name="")


def _advance(machine: m.Machine, event: m.Event, last: float) -> tuple[m.Machine, list[m.Action]]:
    return m.step(machine, event, _clock() - last)


def _perform(socket: Any, actions: list[m.Action]) -> bool:
    """Send what the reducer asked for. False if the socket died trying."""
    for action in actions:
        if isinstance(action, m.Send):
            try:
                socket.send(action.frame)
            except Exception as exc:
                logger.info("Send failed: %s", exc)
                return False
    return True


def _finished(actions: list[m.Action]) -> bool:
    return any(isinstance(action, m.Finish) for action in actions)


def _delay(actions: list[m.Action]) -> float:
    for action in actions:
        if isinstance(action, m.Reconnect):
            return action.after
    return m.backoff(0)


def _dropped(socket: Any) -> bool:
    """Whether the peer has gone, without blocking to find out."""
    try:
        socket.recv(timeout=0)
    except TimeoutError:
        return False
    except Exception:
        return True
    return False


def _drop(socket: Any) -> None:
    """Let go of a socket that is no longer any use."""
    _close(socket)


def _close(socket: Any) -> None:
    if socket is None:
        return
    with contextlib.suppress(Exception):
        socket.close()


def _connect(base_url: str, token: str, session_id: str) -> Any:
    """Open the real socket. Imported here, never at module scope.

    ``commands/__init__.py`` imports every command module eagerly, so a
    top-level ``import websockets`` would be paid by every hook invocation —
    including the unbound ones that make no connection at all and are
    supposed to cost nothing.
    """
    from websockets.sync.client import connect as ws_connect

    return ws_connect(
        protocol.ws_url(base_url, session_id),
        additional_headers={"Authorization": f"Bearer {token}"},
        user_agent_header="cylist-cli",
        open_timeout=CONNECT_TIMEOUT,
        ping_interval=PING_INTERVAL,
        ping_timeout=PING_TIMEOUT,
        close_timeout=2,
    )


def _configure_logging(session_id: str) -> None:
    """One file per daemon life, truncated at start.

    Truncated rather than appended so a long-lived machine cannot accumulate
    an unbounded log from a process nobody remembers starting.
    """
    handler = logging.FileHandler(log_path(session_id), mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if os.environ.get("CYLIST_HOOK_DEBUG") == "1" else logging.INFO)
