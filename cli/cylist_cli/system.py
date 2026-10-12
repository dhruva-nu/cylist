"""What differs between the machines this runs on.

Every platform assumption that is not already somebody's own business lives
here, so that "does this work on Windows" is one file to read rather than a
search for ``fcntl`` and ``getuid``.

Two of them are *not* here, deliberately, because they belong to the module
that owns the decision:

* **The presence channel.** A unix socket on Linux and macOS, a loopback port
  with a shared secret on Windows — :mod:`cylist_cli.presence.ipc` chooses,
  because the choice is inseparable from how the channel is protected.
* **How private a file is.** ``chmod`` moves only the read-only bit on
  Windows, so what protects a token there is the ACL on the user's profile.
  :func:`cylist_cli.config.describe_protection` says that, next to the code
  that writes the file.

What is left is the questions with no natural owner: where machine-local
state goes, how to write a file there that only this user can read, how to
invoke this CLI again from a process that did not inherit its PATH, and how to
render a command line that a shell will read back.
"""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

PRIVATE_MODE = 0o600


def windows() -> bool:
    """Whether this is Windows, asked *now* rather than at import.

    A function and not a constant on purpose: a module-level
    ``sys.platform == "win32"`` is fixed the moment the module loads, and the
    suite reaches the Windows branch of all of this by patching
    ``sys.platform`` at call time — see ``as_windows`` in
    ``tests/test_presence_portability.py``. A constant would make every
    Windows path in this file unreachable from a Linux run, which is how the
    Windows paths stopped working in the first place.
    """
    return sys.platform == "win32"


def state_dir() -> Path:
    """``$XDG_STATE_HOME/cylist``, or the platform's equivalent.

    ``XDG_STATE_HOME`` is honoured everywhere, not only where a system sets
    it: it is how the suite redirects this, and a Windows user who has one set
    has said something deliberate. Otherwise ``%LOCALAPPDATA%`` on Windows and
    ``~/.local/state`` elsewhere.

    Machine-local bookkeeping: which session is on which card, which address
    last answered, a daemon's log. Nothing here matters if it is lost — which
    is why one rule covers all of it rather than each caller choosing.
    """
    root = os.environ.get("XDG_STATE_HOME")
    if root:
        base = Path(root)
    elif windows():
        local = os.environ.get("LOCALAPPDATA")
        base = Path(local) if local else Path.home() / "AppData" / "Local"
    else:
        base = Path.home() / ".local" / "state"
    return base / "cylist"


def write_private(path: Path, content: str) -> None:
    """Write ``content`` to a file only this user can read, from its first byte.

    Created 0600 rather than created and then chmod-ed: between those two
    calls a token would briefly be world-readable, and on a shared machine
    "briefly" is long enough. An existing file keeps its old mode through
    ``O_CREAT``, so it is tightened as well — and before anything is written
    into it, because the content is new even when the file is not.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, PRIVATE_MODE)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        path.chmod(PRIVATE_MODE)
        handle.write(content)


def cylist_argv() -> list[str]:
    """How to invoke this CLI, as an argv, without the subcommand.

    Absolute, because a hook runs with whatever PATH the harness happened to
    inherit, and ``uv tool install`` and a venv put ``cylist`` in different
    places. Falls back to the module form for a checkout run with ``uv run``.

    An argv rather than a string because two callers want it and they want
    different things: the installer writes a shell command into settings.json,
    and the supervisor execs a list.
    """
    found = shutil.which("cylist")
    if found and _is_executable_image(found):
        return [str(Path(found).resolve())]
    invoked = Path(sys.argv[0])
    if invoked.name == "cylist" and invoked.is_absolute():
        return [str(invoked.resolve())]
    return [str(Path(sys.executable).resolve()), "-m", "cylist_cli"]


def _is_executable_image(found: str) -> bool:
    """Whether ``CreateProcess`` can run this directly, which on Windows is a question.

    ``PATHEXT`` puts ``.EXE`` ahead of ``.CMD``, so the shims pip and uv write
    are found first and this is almost always true. When it is not — a batch
    shim and no exe beside it — the module form is taken instead, because a
    ``.cmd`` needs ``cmd.exe`` wrapped round it and a daemon does not need a
    ``cmd.exe`` sitting in front of it for its whole life.
    """
    if not windows():
        return True
    return Path(found).suffix.lower() not in {".cmd", ".bat"}


def shell_command(argv: Sequence[str]) -> str:
    """Render an argv as a command line somebody can paste and a shell will read.

    For *showing* a command — the ``uv tool install`` or ``claude mcp add``
    that ``cylist setup`` prints when it cannot run one itself. The path in it
    is not ours to choose: ``uv tool install`` and a virtualenv put ``cylist``
    in different places, and on Windows one of them has a space in it.

    Not what writes the hook into ``settings.json``: that string has to be
    recognised again by
    :data:`cylist_cli.commands.hook_install.OUR_HOOK_COMMAND` on the next
    install, so its quoting is that module's to decide.
    """
    if not windows():
        return " ".join(shlex.quote(part) for part in argv)
    # `cmd.exe` does not treat single quotes as quoting at all, and a Windows
    # path cannot contain a double quote — the filesystem forbids it — so
    # there is nothing left here to escape.
    return " ".join(f'"{part}"' if " " in part else part for part in argv)
