"""What the hook and its daemon do on a machine that is not this one.

Windows has no ``socket.AF_UNIX`` in CPython, no ``fcntl``, no signal that a
running process can catch, and paths with spaces in them as the ordinary
case. Each of those is a separate way for the presence daemon to be unusable
there, and each is tested here rather than on the platform — by driving the
branch directly, so that a Linux run is still a run of this code.

The loopback transport is not tested only here: ``conftest.ipc_transport``
puts every presence test through it as well.
"""

from __future__ import annotations

import json
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest

from cylist_cli.commands import hook
from cylist_cli.presence import ipc, protocol, supervisor
from tests import fake_daemon

SESSION = "01a08bbf-994b-743b-989c-e07e09e66add"


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    (tmp_path / "run").mkdir(mode=0o700, exist_ok=True)
    return tmp_path


def as_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    """Take the Windows branch of everything that asks at call time.

    Every platform test in this module reads ``sys.platform`` when it runs,
    which is what makes them reachable from here. The one exception is the
    ``msvcrt``/``fcntl`` import in :mod:`.supervisor`, which happens once at
    import and is covered by :class:`TestImportingOnAPlatformWithoutFcntl`.
    """
    monkeypatch.setattr(sys, "platform", "win32")


# --- The one that stopped the CLI starting at all ---------------------------


class TestImportingOnAPlatformWithoutFcntl:
    def test_the_cli_still_loads(self) -> None:
        """``fcntl`` is Unix-only, and importing it unconditionally is not a
        degraded presence daemon — it is ``cylist --help`` raising
        ``ModuleNotFoundError``, because ``commands/__init__.py`` imports
        every command module eagerly and the hook reaches the supervisor.

        Proved by forbidding the module rather than by reading the source: a
        conditional import that is still wrong in some third way would pass
        a grep and fail this.
        """
        probe = textwrap.dedent(
            """
            import sys, types

            # Import once as this platform really is, so that the standard
            # library — shutil and friends, which read sys.platform at import
            # and are not the thing under test — is already loaded. Then
            # forget only our own modules and do it again as Windows.
            import cylist_cli.commands, cylist_cli.main
            for name in [n for n in sys.modules if n.startswith("cylist_cli")]:
                del sys.modules[name]

            sys.platform = "win32"
            windows_only = types.ModuleType("msvcrt")
            windows_only.locking = lambda *args: None
            windows_only.LK_NBLCK, windows_only.LK_UNLCK = 1, 0
            sys.modules["msvcrt"] = windows_only

            class Absent:
                def find_spec(self, name, path=None, target=None):
                    if name == "fcntl":
                        raise ImportError("no module named 'fcntl' on this platform")
                    return None

            sys.meta_path.insert(0, Absent())
            sys.modules.pop("fcntl", None)

            import cylist_cli.commands
            import cylist_cli.main
            print("loaded")
            """
        )
        result = subprocess.run(  # noqa: S603 - our own interpreter, our own source
            [sys.executable, "-c", probe], capture_output=True, text=True
        )

        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == "loaded"


# --- The loopback carrier ----------------------------------------------------


class TestTheLoopbackTransport:
    @pytest.fixture(autouse=True)
    def over_tcp(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(ipc.TRANSPORT_ENV, "tcp")

    def test_it_is_chosen_where_there_is_no_af_unix(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(ipc.TRANSPORT_ENV, raising=False)
        monkeypatch.delattr(socket, "AF_UNIX", raising=False)

        assert ipc.transport() == "tcp"

    def test_a_message_gets_through(self) -> None:
        with fake_daemon.FakeDaemon(SESSION) as daemon:
            ack = ipc.send(SESSION, protocol.state_message(SESSION, "ATL-1", "working", None, "x"))

        assert ack is not None
        assert ack["ok"] is True
        assert daemon.received[0]["task"] == "ATL-1"

    def test_it_listens_on_the_loopback_and_not_on_the_network(self) -> None:
        """A wildcard bind would put a channel into a process holding a
        bearer token onto whatever network the laptop is on."""
        listener = ipc.Listener(SESSION)
        try:
            host, _ = listener._sock.getsockname()
        finally:
            listener.close()

        assert host == "127.0.0.1"

    def test_the_endpoint_file_is_private_and_names_a_port_and_a_secret(self) -> None:
        listener = ipc.Listener(SESSION)
        try:
            published = json.loads(ipc.endpoint_path(SESSION).read_text())
            mode = ipc.endpoint_path(SESSION).stat().st_mode
        finally:
            listener.close()

        assert isinstance(published["port"], int)
        assert len(published["secret"]) >= 32
        if sys.platform != "win32":
            assert not mode & 0o077

    def test_a_caller_without_the_secret_is_refused(self) -> None:
        """On the loopback the token is the only thing between this daemon
        and every other process on the machine, because a port has no mode."""
        heard: list[dict[str, Any]] = []
        with fake_daemon.FakeDaemon(SESSION) as daemon:
            port, _ = ipc._read_endpoint(SESSION)
            client = socket.create_connection(("127.0.0.1", port), timeout=1.0)
            try:
                line = protocol.end_message(SESSION, "session_ended")
                client.sendall(b"not-the-secret\n" + line.encode())
                reply = json.loads(ipc._Reader(client).line())
            finally:
                client.close()
            heard = daemon.received

        assert reply["ok"] is False
        assert reply["why"] == "unauthorised"
        assert heard == [], "the message was parsed before the caller was turned away"

    def test_each_daemon_gets_a_fresh_secret(self) -> None:
        """A secret reused across daemons would outlive the process it
        authorised, which is the property a mode bit would have given away."""
        secrets = []
        for _ in range(2):
            listener = ipc.Listener(SESSION)
            secrets.append(json.loads(ipc.endpoint_path(SESSION).read_text())["secret"])
            listener.close()

        assert secrets[0] != secrets[1]

    def test_a_stale_endpoint_reads_as_no_daemon(self) -> None:
        """A port nobody is on any more must look exactly like the absence of
        a daemon, because the hook's answer to both is to use HTTP."""
        with fake_daemon.FakeDaemon(SESSION):
            pass  # published, then closed

        assert ipc.send(SESSION, protocol.end_message(SESSION, "x")) is None

    def test_an_unreadable_endpoint_reads_as_no_daemon(self) -> None:
        ipc.endpoint_path(SESSION).write_text("half a fi")

        assert ipc.send(SESSION, protocol.end_message(SESSION, "x")) is None


# --- Where things live -------------------------------------------------------


class TestWindowsPaths:
    def test_session_state_goes_under_local_app_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_windows(monkeypatch)
        monkeypatch.delenv("XDG_STATE_HOME", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

        assert hook.sessions_dir() == tmp_path / "Local" / "cylist" / "sessions"

    def test_endpoints_go_under_local_app_data(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        as_windows(monkeypatch)
        monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

        assert ipc.runtime_dir() == tmp_path / "Local" / "cylist" / "run"

    def test_an_explicit_xdg_variable_still_wins(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Honoured on every platform, so one environment variable isolates
        the suite everywhere — and so that somebody who set it meant it."""
        as_windows(monkeypatch)
        monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))

        assert hook.sessions_dir() == tmp_path / "state" / "cylist" / "sessions"

    def test_a_detached_daemon_does_not_sit_on_the_caller_s_drive(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``/`` on Windows means the root of whichever drive the prompt was
        on, and a daemon outlives the prompt."""
        as_windows(monkeypatch)
        monkeypatch.setenv("SYSTEMDRIVE", "D:")

        assert supervisor.detached_cwd() == "D:\\"

    def test_posix_still_gets_the_filesystem_root(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        assert supervisor.detached_cwd() == "/"


# --- Starting and stopping ---------------------------------------------------


class TestDetaching:
    def test_posix_gets_a_new_session(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "platform", "linux")

        assert supervisor._detach() == {"start_new_session": True}

    def test_windows_gets_detached_process_and_no_console(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``start_new_session`` is POSIX-only and silently ignored on
        Windows, which is the worst of both — it looks set and does nothing.
        ``CREATE_NO_WINDOW`` is the difference between a background daemon
        and a console blinking open on every prompt.
        """
        as_windows(monkeypatch)
        monkeypatch.setattr(subprocess, "DETACHED_PROCESS", 0x8, raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
        monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", 0x8000000, raising=False)

        flags = supervisor._detach()

        assert "start_new_session" not in flags
        assert flags["creationflags"] == 0x8 | 0x200 | 0x8000000


class TestStopping:
    def test_it_reads_the_pid_and_not_the_whole_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The pid file is two lines — the pid and the session it serves —
        and parsing the whole of it as an integer made ``hook stop`` report
        that there was nothing to stop, every time, whatever was running.

        Not faked to either platform: Windows asks over the socket first,
        finds no endpoint, and falls through to this same path.
        """
        signalled: list[int] = []
        monkeypatch.setattr("os.kill", lambda pid, _: signalled.append(pid))
        supervisor.pid_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
        supervisor.pid_path(SESSION).write_text(f"424242\n{SESSION}\n")

        assert supervisor.stop(SESSION) is True
        assert signalled == [424242]

    def test_nothing_to_stop_is_not_an_error(self) -> None:
        assert supervisor.stop(SESSION) is False

    def test_windows_asks_over_the_socket_before_reaching_for_terminate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``os.kill`` on Windows is ``TerminateProcess``: no handler runs,
        no ``finally`` runs, and the session is left on the board looking
        live until the server's own idle window closes it. The socket is the
        only graceful stop there is, so it is tried first.
        """
        as_windows(monkeypatch)
        monkeypatch.setenv(ipc.TRANSPORT_ENV, "tcp")
        monkeypatch.setattr("os.kill", _never_called)
        supervisor.pid_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
        supervisor.pid_path(SESSION).write_text(f"424242\n{SESSION}\n")

        with fake_daemon.FakeDaemon(SESSION) as daemon:
            stopped = supervisor.stop(SESSION)

        assert stopped is True
        assert daemon.received[0]["t"] == "end"

    def test_windows_clears_up_after_a_process_it_had_to_terminate(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Nothing ran in the daemon to unlink these. A hook that found the
        endpoint afterwards would dial a dead port and wait out its timeout
        for no reason.
        """
        as_windows(monkeypatch)
        monkeypatch.setenv(ipc.TRANSPORT_ENV, "tcp")
        monkeypatch.setattr("os.kill", lambda pid, sig: None)
        supervisor.pid_path(SESSION).parent.mkdir(parents=True, exist_ok=True)
        supervisor.pid_path(SESSION).write_text(f"424242\n{SESSION}\n")
        ipc.endpoint_path(SESSION).write_text(json.dumps({"port": 1, "secret": "s"}))

        assert supervisor.stop(SESSION) is True
        assert not supervisor.pid_path(SESSION).exists()
        assert not ipc.endpoint_path(SESSION).exists()


def _never_called(*_: object) -> None:
    raise AssertionError("the graceful path was skipped")


# --- The command written into settings.json ----------------------------------


class TestTheInstalledCommand:
    def test_a_path_with_spaces_is_quoted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Windows profiles are named after people, and people have spaces in
        their names. Unquoted, Claude Code would run ``C:\\Users\\Given``.
        """
        monkeypatch.setattr(
            hook, "cylist_argv", lambda: ["C:\\Users\\Given Name\\.local\\bin\\cylist.exe"]
        )

        assert hook.hook_command() == '"C:\\Users\\Given Name\\.local\\bin\\cylist.exe" hook'

    def test_a_path_without_spaces_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """The command already in everybody's settings.json, unchanged — an
        installer that rewrote every POSIX entry would be churn, not a port.
        """
        monkeypatch.setattr(hook, "cylist_argv", lambda: ["/home/somebody/.local/bin/cylist"])

        assert hook.hook_command() == "/home/somebody/.local/bin/cylist hook"

    @pytest.mark.parametrize(
        "command",
        [
            "/home/somebody/.local/bin/cylist hook",
            '"C:\\Users\\Given Name\\.local\\bin\\cylist.exe" hook',
            "C:\\Users\\d\\AppData\\Local\\bin\\cylist.exe hook",
            "/usr/bin/python3 -m cylist_cli hook",
            '"C:\\Program Files\\Python\\python.exe" -m cylist_cli hook',
        ],
    )
    def test_every_shape_of_our_own_install_is_recognised(self, command: str) -> None:
        """An installer that cannot see its own earlier work adds a second
        copy of the hook on every run, and quoting moved the end of the
        string that the old suffix test was looking at."""
        assert hook._is_ours(command) is True

    @pytest.mark.parametrize(
        "command",
        [
            "/usr/bin/somebody-elses-tool hook",
            "/home/somebody/bin/mycylist hook",
            "/home/somebody/.local/bin/cylist status",
            "",
        ],
    )
    def test_somebody_else_s_hook_is_left_alone(self, command: str) -> None:
        assert hook._is_ours(command) is False

    def test_a_batch_shim_is_not_handed_to_createprocess(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """``CreateProcess`` cannot run a ``.cmd``. The module form always
        can, because the process asking is already running this package.
        """
        as_windows(monkeypatch)
        monkeypatch.setattr(shutil, "which", lambda _: "C:\\shims\\cylist.cmd")
        monkeypatch.setattr(sys, "argv", ["cylist"])

        argv = hook.cylist_argv()

        assert argv[-2:] == ["-m", "cylist_cli"]

    def test_an_exe_is_used_directly(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        as_windows(monkeypatch)
        shim = tmp_path / "cylist.exe"
        shim.touch()
        monkeypatch.setattr(shutil, "which", lambda _: str(shim))

        assert hook.cylist_argv() == [str(shim)]


class TestADaemonThatCannotListen:
    """The hook detached this process onto DEVNULL, so a traceback on stderr
    goes nowhere. An empty log beside a pid file and no process is the
    hardest possible way to learn that a path was too long or a port refused
    — and both of those are real: AF_UNIX caps near a hundred bytes, and a
    loopback bind is something a Windows machine can be configured to deny.
    """

    def test_it_says_so_in_its_log_instead_of_vanishing(
        self, isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from cylist_cli.config import Config
        from cylist_cli.context import Context
        from cylist_cli.presence import daemon

        def refuse(_: str) -> ipc.Listener:
            raise OSError("AF_UNIX path too long")

        monkeypatch.setattr(ipc, "Listener", refuse)
        ctx = Context(config=Config(url="http://cylist.test", token="cyl_x", token_source="env"))

        assert daemon.run(ctx, SESSION) == 0

        written = daemon.log_path(SESSION).read_text()
        assert "Could not listen for hooks" in written
        assert "AF_UNIX path too long" in written

    def test_it_leaves_nothing_behind_for_a_hook_to_find(
        self, isolated: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pid file outliving the process would put a dead session in
        ``cylist hook status`` until something else cleared it."""
        from cylist_cli.config import Config
        from cylist_cli.context import Context
        from cylist_cli.presence import daemon

        def refuse(_: str) -> ipc.Listener:
            raise OSError("no")

        monkeypatch.setattr(ipc, "Listener", refuse)
        ctx = Context(config=Config(url="http://cylist.test", token="cyl_x", token_source="env"))

        daemon.run(ctx, SESSION)

        assert not supervisor.pid_path(SESSION).exists()
        assert supervisor.is_running(SESSION) is False
