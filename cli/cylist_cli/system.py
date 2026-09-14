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

What is left is the two questions with no natural owner: where machine-local
state goes, and how to render a command line that a shell will read back.
"""

from __future__ import annotations

import os
import shlex
import sys
from collections.abc import Sequence
from pathlib import Path


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


def shell_command(argv: Sequence[str]) -> str:
    """Render an argv as a command line somebody can paste and a shell will read.

    For *showing* a command — the ``uv tool install`` or ``claude mcp add``
    that ``cylist setup`` prints when it cannot run one itself. The path in it
    is not ours to choose: ``uv tool install`` and a virtualenv put ``cylist``
    in different places, and on Windows one of them has a space in it.

    Not what writes the hook into ``settings.json``: that string has to be
    recognised again by :data:`cylist_cli.commands.hook.OURS` on the next
    install, so its quoting is that module's to decide.
    """
    if not windows():
        return " ".join(shlex.quote(part) for part in argv)
    # `cmd.exe` does not treat single quotes as quoting at all, and a Windows
    # path cannot contain a double quote — the filesystem forbids it — so
    # there is nothing left here to escape.
    return " ".join(f'"{part}"' if " " in part else part for part in argv)
