"""The process that holds the socket.

Three threads and a queue. The main thread owns the WebSocket exclusively
and runs the reducer; a second thread accepts on the hook socket and pushes
what the hooks say onto the queue; a third follows the session's transcript
and pushes what the agent did onto the same queue, for the card's feed.
Nothing else is shared, so there is no lock anywhere in here.

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
from collections.abc import Callable
from pathlib import Path
from typing import Any

from cylist_cli import endpoints, presence
from cylist_cli.context import Context
from cylist_cli.presence import ipc, protocol, supervisor, transcript
from cylist_cli.presence import machine as m
from cylist_cli.presence import owner as owner_module

logger = logging.getLogger("cylist.presence")

CONNECT_TIMEOUT = 5.0
PING_INTERVAL = 20.0
PING_TIMEOUT = 20.0
"""Below any plausible proxy idle timeout, and together a ~40s budget for
noticing that a connection has died without saying so."""

TICK = 1.0
"""How often the reducer is given a chance to notice that time has passed."""

FOLLOW_EVERY = 0.5
"""How often the transcript is looked at. Fast enough that a feed reads as
live; slow enough that a stat a half-second is all a quiet session costs."""

ASK_WAIT = 580.0
"""Longest the daemon holds a permission hook waiting on the board, in seconds.

Inside the 600 the hook is installed with, so the daemon is the one that gives
up — tidily, telling the server — rather than the harness killing a hook that
then never hears why; and inside the 590 the server keeps the prompt
answerable, so nobody answers one this has already stopped listening for."""

ASK_POLL = 0.25
"""How often a waiting ask looks for its answer and for its hook hanging up."""


def log_path(session_id: str) -> Path:
    """This session's log, with the directory it goes in made if need be."""
    path = presence.daemon_log(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _find_boottime_clock() -> Callable[[], float] | None:
    """A clock that counts the time the machine spent asleep, if there is one.

    ``monotonic`` stops while a laptop is suspended, so a lid closed for
    eight hours on a waiting session would come back believing no time had
    passed. ``BOOTTIME`` does not, which is what the idle window needs.

    It is a Linux clock, and it is looked up by name because neither the
    function nor the constant exists on Windows. Resolved once at import: it
    cannot appear later, and :func:`_clock` is called on every tick.
    """
    reader: Any = getattr(time, "clock_gettime", None)
    clock_id = getattr(time, "CLOCK_BOOTTIME", None)
    if reader is None or clock_id is None:
        return None
    try:
        reader(clock_id)
    except OSError:  # pragma: no cover - a kernel that names it and refuses it
        return None
    return lambda: float(reader(clock_id))


_BOOTTIME = _find_boottime_clock()


def _clock() -> float:
    """Seconds, from the best clock this platform has.

    macOS and Windows fall back to ``monotonic`` and count the idle window
    from the wake rather than from the last hook. That is a defensible answer
    rather than a broken one: a session that was waiting on a person before
    the lid closed is still waiting on them afterwards, and five more minutes
    of a card saying so costs nothing.
    """
    return _BOOTTIME() if _BOOTTIME is not None else time.monotonic()


def run(
    ctx: Context,
    session_id: str,
    *,
    connect: Any = None,
    owner: owner_module.Owner | None = None,
) -> int:
    """Hold one session's socket until it ends. Always returns 0.

    ``connect`` is injectable so the loop can be driven in a test against a
    fake connection, with no network and no sleeping.

    ``owner`` is the ``claude`` process this session belongs to, found by the
    hook that started this daemon and passed on the command line. Given one,
    the daemon ends when that process does and never on a clock. Given none —
    a platform that would not say — the idle window in :mod:`.machine` stands,
    which is what it has always done.
    """
    lock = supervisor.Singleton(session_id)
    if not lock.acquire():
        # Another daemon owns this session. Not an error: two hooks racing to
        # start one is the ordinary case.
        return 0

    _configure_logging(session_id)
    supervisor.write_pid(session_id)
    events: queue.Queue[m.Event] = queue.Queue()
    state = _Shared(session_id)

    try:
        listener = ipc.Listener(session_id)
    except OSError:
        # There is nowhere for the hooks to reach us, so there is no daemon
        # to be. Logged rather than raised because nobody is reading this
        # process's stderr — it went to DEVNULL when the hook detached it,
        # and an empty log beside a vanished process is the hardest possible
        # way to find out that a path was too long or a port refused.
        logger.exception("Could not listen for hooks; this daemon is no use")
        supervisor.clear_pid(session_id)
        lock.release()
        return 0

    accepting = threading.Thread(
        target=listener.serve,
        args=(
            lambda message: _receive(message, state, events),
            lambda message, gone: _ask(message, gone, state, events),
        ),
        name="cylist-ipc",
        daemon=True,
    )
    accepting.start()

    stopped = threading.Event()
    following = threading.Thread(
        target=_follow,
        args=(state, events, stopped),
        name="cylist-transcript",
        daemon=True,
    )
    following.start()

    def hang_up(*_: object) -> None:
        events.put(m.HookEnd("session_ended"))

    _on_being_asked_to_stop(hang_up)

    try:
        _loop(ctx, session_id, events, state, connect=connect, owner=owner)
    except Exception:
        logger.exception("The presence daemon stopped on an error")
    finally:
        stopped.set()
        listener.close()
        supervisor.clear_pid(session_id)
        lock.release()
    return 0


class _Shared:
    """The little the IPC thread and the main thread both look at.

    Written by the main thread, read by the IPC thread, and only ever a
    couple of strings — so a plain object is enough and a lock would be
    ceremony. Nothing here decides anything; the reducer does that.

    ``answers`` is the exception, and has its own lock: it is where the main
    thread hands a permission's answer to the thread holding that hook.
    """

    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        self.task: str | None = None
        self.link: str = m.Link.CONNECTING.value
        self.transcript: str | None = None
        """Written by the IPC thread, read by the follower: the one exception
        to "written by the main thread", and still a single string."""
        self.answers = _Answers()


class _Answers:
    """Hooks waiting on the board, by prompt, and the way to hand them an answer."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._waiting: dict[str, queue.Queue[dict[str, Any]]] = {}

    def expect(self, prompt_id: str) -> queue.Queue[dict[str, Any]]:
        inbox: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._waiting[prompt_id] = inbox
        return inbox

    def deliver(self, prompt_id: str, answer: dict[str, Any]) -> bool:
        """Hand a waiting hook its answer. False if nobody is waiting for it."""
        with self._lock:
            inbox = self._waiting.pop(prompt_id, None)
        if inbox is None:
            return False
        inbox.put_nowait(answer)
        return True

    def release_all(self, answer: dict[str, Any]) -> int:
        """Hand every waiting hook the same answer. Returns how many there were."""
        with self._lock:
            waiting, self._waiting = self._waiting, {}
        for inbox in waiting.values():
            inbox.put_nowait(answer)
        return len(waiting)

    def forget(self, prompt_id: str) -> None:
        with self._lock:
            self._waiting.pop(prompt_id, None)


def _receive(message: dict[str, Any], state: _Shared, events: queue.Queue[m.Event]) -> str:
    """Turn one IPC message into an event, and answer the hook."""
    if message.get("session") != state.session_id:
        # A socket path collision, or a reused runtime directory. Cheap to
        # check, and the alternative is one conversation's state landing on
        # another's card.
        return protocol.refusal("wrong_session")

    transcript = message.get("transcript")
    if isinstance(transcript, str) and transcript:
        state.transcript = transcript

    kind = message.get("t")
    if kind == "state":
        events.put(
            m.HookState(
                state=str(message.get("state") or "working"),
                reason=message.get("reason"),
                client_name=str(message.get("client_name") or ""),
                task=str(message.get("task") or ""),
                note=_note(message.get("note")),
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
    elif kind == "ran":
        # The terminal answered whatever permission is waiting here, so the
        # hooks still holding one can stop: nobody on the board decides it now.
        state.answers.release_all({"status": "withdrawn", "key": None, "answer": None})
        events.put(m.ToolRan())
    # "ping" asks for the ack and nothing else.
    return protocol.ack(state.link, state.task, os.getpid())


def _ask(
    message: dict[str, Any],
    gone: Callable[[], bool],
    state: _Shared,
    events: queue.Queue[m.Event],
) -> str:
    """Put a permission on the card and hold the hook until it is decided.

    Runs on a thread of its own per ask (see :meth:`ipc.Listener.serve`).
    The ways out: the board answers, and the ack carries it; a ``ran`` says
    the terminal answered first, and the ack says "withdrawn"; the hook hangs
    up, and the server is told; or :data:`ASK_WAIT` runs out, the server is
    told the prompt expired, and the ack says nobody answered — the hook then
    leaves the terminal to it.
    """
    if message.get("session") != state.session_id:
        return protocol.refusal("wrong_session")
    transcript = message.get("transcript")
    if isinstance(transcript, str) and transcript:
        state.transcript = transcript

    prompt_id = str(message["prompt_id"])
    inbox = state.answers.expect(prompt_id)
    tool = message.get("tool")
    events.put(
        m.HookAsk(
            prompt_id=prompt_id,
            text=str(message["text"]),
            tool=tool if isinstance(tool, str) else None,
            task=str(message.get("task") or ""),
            client_name=str(message.get("client_name") or ""),
        )
    )
    deadline = _clock() + ASK_WAIT
    try:
        while True:
            try:
                answer = inbox.get(timeout=ASK_POLL)
            except queue.Empty:
                pass
            else:
                return protocol.ack(state.link, state.task, os.getpid(), answer=answer)
            if gone():
                logger.info("The hook for %s hung up before the board answered", prompt_id)
                events.put(m.AskDropped(prompt_id, "answered_in_terminal"))
                return protocol.refusal("hung_up")
            if _clock() >= deadline:
                logger.info("Nobody answered %s on the board in time", prompt_id)
                events.put(m.AskDropped(prompt_id, "expired"))
                return protocol.ack(state.link, state.task, os.getpid())
    finally:
        state.answers.forget(prompt_id)


def _follow(state: _Shared, events: queue.Queue[m.Event], stopped: threading.Event) -> None:
    """Follow the session's transcript, and queue what the agent did.

    Waits for a hook to say which file — every event carries it — and starts
    at that file's end. A different path later (a ``/clear`` keeps the
    daemon's session id only if it keeps the conversation, so in practice
    this is a resumed one) is followed from *its* end in turn. Nothing here
    can stop the daemon: a transcript that cannot be read is a feed that
    stays empty, which is what a card had before this existed.
    """
    tail: transcript.Tail | None = None
    narrator = transcript.Narrator()
    while not stopped.wait(FOLLOW_EVERY):
        path = state.transcript
        if path is None:
            continue
        if tail is None or tail.path != path:
            tail = transcript.Tail(path)
            logger.info("Following %s", path)
            continue
        try:
            found = [entry.wire() for line in tail.read() for entry in narrator.read_line(line)]
        except Exception:  # pragma: no cover - defensive; see the docstring
            logger.exception("Could not read the transcript")
            continue
        if found:
            events.put(m.FeedLines(tuple(found)))


def _note(value: object) -> str | None:
    """A note off the IPC line: a non-empty string, or nothing."""
    return value if isinstance(value, str) and value.strip() else None


def _loop(
    ctx: Context,
    session_id: str,
    events: queue.Queue[m.Event],
    state: _Shared,
    *,
    connect: Any = None,
    owner: owner_module.Owner | None = None,
) -> None:
    """Connect, pump events through the reducer, and do what it says."""
    connect = connect or _connect
    token = ctx.config.token
    if not token:
        logger.info("No token configured; nothing to report with.")
        return

    watch: owner_module.Watch | None = None
    if owner is not None:
        watch = owner_module.Watch(owner, _clock())
        logger.info("Owned by pid %s, started %s", owner.pid, owner.started or "unknown")

    # The first event is the one after the one that started us, so the machine
    # can be built before anything else is read.
    first = _first_event(events, watch)
    if isinstance(first, m.HookEnd | m.OwnerGone):
        return
    reducer = _Reducer(_initial(session_id, first, owned=watch is not None), state)
    if isinstance(first, m.HookAsk):
        # Held, not lost: the link is not up yet, so it waits in the machine
        # and goes out after the level once it is.
        reducer.feed(first)

    socket: Any = None

    # Every address the server answers on, tried in turn. A daemon outlives
    # the network it was started on: a laptop that suspends on the tailnet and
    # wakes on a hotel network has to reconnect somewhere else or spend the
    # rest of the session reporting nothing.
    addresses = endpoints.order(ctx.config.urls)
    attempt = 0

    while True:
        # Nothing to say until we know which card, and a socket opened
        # without one would report on the empty reference and be closed for
        # it. Wait instead; the next hook event says where.
        if socket is None and reducer.machine.task and _clock() >= reducer.retry_at:
            base_url = addresses[attempt % len(addresses)]
            try:
                # Re-read the token every attempt, so a fresh `cylist setup`
                # heals a daemon that is already running.
                socket = connect(base_url, ctx.config.token or token, session_id)
            except Exception as exc:
                attempt += 1
                logger.info("Connect to %s failed: %s", base_url, exc)
                if reducer.link_lost():
                    return
                continue
            endpoints.remember(base_url)
            if not _perform(socket, reducer.feed(m.LinkUp())):
                _close(socket)
                socket = None
                continue

        actions = reducer.feed(_next_event(events, watch))

        if socket is not None and not _perform(socket, actions):
            _close(socket)
            socket = None
            if reducer.link_lost():
                return
            continue

        if _finished(actions):
            _close(socket)
            return

        if socket is not None:
            dropped, heard = _drain(socket)
            for frame in heard:
                if not _perform(socket, reducer.feed(_hear(frame, state))):
                    dropped = True
                    break
            if dropped:
                _close(socket)
                socket = None
                if reducer.link_lost():
                    return


class _Reducer:
    """The machine, the clock it is stepped by, and when to try connecting next.

    Every step has to do the same three things — measure the time since the
    last one, keep the new machine, and let the IPC thread see where it now
    stands — so they are done here once rather than at every place the loop
    steps it.
    """

    def __init__(self, machine: m.Machine, state: _Shared) -> None:
        self.machine = machine
        self.retry_at = 0.0
        self._state = state
        self._last = _clock()
        state.task = machine.task

    def feed(self, event: m.Event) -> list[m.Action]:
        """Step the machine by one event, and return what it says to do."""
        self.machine, actions = m.step(self.machine, event, _clock() - self._last)
        self._last = _clock()
        self._state.task = self.machine.task
        self._state.link = self.machine.link.value
        return actions

    def link_lost(self) -> bool:
        """Tell the machine the socket is gone. True if it says to give up."""
        actions = self.feed(m.LinkDown())
        self.retry_at = self._last + _delay(actions)
        return _finished(actions)


def _first_event(events: queue.Queue[m.Event], watch: owner_module.Watch | None) -> m.Event:
    """Wait for the event this daemon's machine is built from.

    Polled rather than blocked on, so that the owner check is running before
    there is a machine to run it for. A daemon is spawned by a hook that has
    already reported over HTTP and then says nothing more; if the terminal
    closed in that gap, a blocking ``get`` would leave this process sitting on
    an empty queue for as long as the machine stayed up.
    """
    while True:
        event = _next_event(events, watch)
        if not isinstance(event, m.Tick):
            return event


def _next_event(events: queue.Queue[m.Event], watch: owner_module.Watch | None) -> m.Event:
    """What the hooks said next, or what a second of silence means.

    Silence used to mean only "time has passed". Where the owner is known it
    is also the moment to ask whether the terminal is still open, which is
    the question that keeps nine quiet sessions out of ten on the board. It
    is asked here rather than in the reducer because it is a system call, and
    only on the quiet path because a hook event is itself proof of life.
    """
    try:
        return events.get(timeout=TICK)
    except queue.Empty:
        pass
    if watch is not None and watch.gone(_clock()):
        logger.info("Owner pid %s has gone; ending the session", watch.owner.pid)
        return m.OwnerGone()
    return m.Tick()


def _initial(session_id: str, first: m.Event, *, owned: bool) -> m.Machine:
    if isinstance(first, m.HookBind):
        return m.Machine(
            session=session_id, task=first.task, client_name=first.client_name, owned=owned
        )
    if isinstance(first, m.HookState):
        # Almost always the real first message: see `HookState.task`.
        return m.Machine(
            session=session_id,
            task=first.task,
            client_name=first.client_name,
            agent=m.Agent.WAITING if first.state == "waiting" else m.Agent.WORKING,
            reason=first.reason,
            note=first.note,
            owned=owned,
        )
    if isinstance(first, m.HookAsk):
        return m.Machine(
            session=session_id, task=first.task, client_name=first.client_name, owned=owned
        )
    return m.Machine(session=session_id, task="", client_name="", owned=owned)


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


def _drain(socket: Any) -> tuple[bool, list[dict[str, Any]]]:
    """Whether the peer has gone, and the frames worth reading it sent meanwhile.

    Never blocks. Most of what the server sends — acks of the level — has
    always been read here only to be thrown away; since CYLIST-89 an
    ``answer`` is kept, because it is a person deciding a permission.
    """
    heard: list[dict[str, Any]] = []
    while True:
        try:
            raw = socket.recv(timeout=0)
        except TimeoutError:
            return False, heard
        except Exception:
            return True, heard
        frame = protocol.parse_server_frame(raw)
        if frame is not None and frame.get("type") == "answer":
            heard.append(frame)


def _hear(frame: dict[str, Any], state: _Shared) -> m.Event:
    """Hand a closed prompt's outcome to the hook waiting on it, and tell the machine."""
    prompt_id = str(frame["prompt_id"])
    status = str(frame.get("status") or "")
    answer = {"status": status, "key": frame.get("key"), "answer": frame.get("answer")}
    if not state.answers.deliver(prompt_id, answer):
        logger.debug("An answer to %s arrived with no hook waiting for it", prompt_id)
    return m.PromptClosed(prompt_id, status)


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


def _on_being_asked_to_stop(hang_up: Callable[..., None]) -> None:
    """End the session tidily on whichever signals this platform delivers.

    SIGTERM is what ``supervisor.stop`` sends on POSIX. On Windows it is
    registrable and never delivered — ``os.kill`` there is
    ``TerminateProcess``, which runs no handler — so SIGBREAK is added, that
    being the one a console Ctrl-Break or ``CREATE_NEW_PROCESS_GROUP`` can
    actually raise. Windows' graceful stop is the ``end`` message on the IPC
    channel; these are what is left for the console cases.
    """
    for name in ("SIGTERM", "SIGINT", "SIGBREAK"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        # ValueError: not the main thread, which is how the suite drives this.
        with contextlib.suppress(ValueError, OSError):
            signal.signal(number, hang_up)


def _configure_logging(session_id: str) -> None:
    """One file per daemon life, truncated at start.

    Truncated rather than appended so a long-lived machine cannot accumulate
    an unbounded log from a process nobody remembers starting.
    """
    handler = logging.FileHandler(log_path(session_id), mode="w")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if os.environ.get("CYLIST_HOOK_DEBUG") == "1" else logging.INFO)
