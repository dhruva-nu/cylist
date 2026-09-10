"""A daemon that is not one, for testing the hook's side of the socket.

Binds the real path the hook will look for and answers on a thread, so the
hook's code runs unmodified — the only pretend part is what is listening. It
can also be told to behave badly, because "the daemon is wedged" and "there
is no daemon" have to produce the same outcome for a prompt and only one of
them is easy to arrange.
"""

from __future__ import annotations

import contextlib
import json
import socket
import threading
from typing import Any

from cylist_cli.presence import ipc, protocol


class FakeDaemon:
    """Listens where a daemon would, and records what it is told."""

    def __init__(self, session_id: str, *, behaviour: str = "ack") -> None:
        self.session_id = session_id
        self.behaviour = behaviour
        self.received: list[dict[str, Any]] = []
        self.path = ipc.socket_path(session_id)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()

    def __enter__(self) -> FakeDaemon:
        if self.behaviour == "absent":
            return self
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(str(self.path))
        self._sock.listen(4)
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._stopping.set()
        if self._sock is not None:
            self._sock.close()
            self._sock = None
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    def _serve(self) -> None:
        # Held locally: `close()` clears the attribute from the main thread,
        # and reading it each time around would race with that.
        listening = self._sock
        assert listening is not None
        # Polled rather than left blocking, because closing a socket does not
        # reliably wake an `accept()` on another thread — which cost every
        # test using this fixture a full second of teardown before anyone
        # noticed it was the fixture and not the code.
        listening.settimeout(0.02)
        while not self._stopping.is_set():
            try:
                client, _ = listening.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                raw = client.recv(protocol.MAX_LINE).decode()
                message = json.loads(raw) if raw.strip() else {}
                self.received.append(message)
                client.sendall(self._reply(message).encode())
            except (OSError, ValueError):
                pass
            finally:
                client.close()

    def _reply(self, message: dict[str, Any]) -> str:
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
