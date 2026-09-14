"""A daemon that is not one, for testing the hook's side of the channel.

Built on the real :class:`ipc.Listener`, so it binds whatever the transport
under test binds and the hook's code runs unmodified — the only pretend part
is what is listening. It can also be told to behave badly, because "the
daemon is wedged" and "there is no daemon" have to produce the same outcome
for a prompt and only one of them is easy to arrange.
"""

from __future__ import annotations

import threading
from typing import Any

from cylist_cli.presence import ipc, protocol


class FakeDaemon:
    """Listens where a daemon would, and records what it is told."""

    def __init__(self, session_id: str, *, behaviour: str = "ack") -> None:
        self.session_id = session_id
        self.behaviour = behaviour
        self.received: list[dict[str, Any]] = []
        self._listener: ipc.Listener | None = None
        self._thread: threading.Thread | None = None

    def __enter__(self) -> FakeDaemon:
        if self.behaviour == "absent":
            return self
        self._listener = ipc.Listener(self.session_id)
        self._thread = threading.Thread(
            target=self._listener.serve, args=(self._handle,), daemon=True
        )
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._listener is not None:
            self._listener.close()
            self._listener = None
        if self._thread is not None:
            # A "hang" is mid-sleep and will not come back inside this; it is
            # a daemon thread and the process is not waiting on it.
            self._thread.join(timeout=1.0)
            self._thread = None

    def _handle(self, message: dict[str, Any]) -> str:
        self.received.append(message)
        if self.behaviour == "hang":
            # Accepts, reads, and answers far too late. Only has to outlast
            # the hook's quarter-second deadline; anything longer is time
            # the suite spends proving the same thing.
            threading.Event().wait(1.0)
            return protocol.ack("live", None, 1)
        if self.behaviour == "wrong_session":
            return protocol.refusal("wrong_session")
        if self.behaviour == "garbage":
            return "not json\n"
        return protocol.ack("live", str(message.get("task") or ""), 4242)
