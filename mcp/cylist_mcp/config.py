"""Configuration: the environment, or the file ``cylist setup`` writes.

An MCP server is started by its client — Claude Code, an IDE, an agent
runtime — with no terminal to prompt at, so there is no login flow here. What
there is now is a second place to read from, and it is the better one.

``CYLIST_URL`` and ``CYLIST_TOKEN`` in the ``env`` block of an MCP client's
configuration used to be the only way, which meant a live token written into
``.mcp.json`` — a file that lives in a repository and gets committed by
accident. So this also reads ``~/.config/cylist/config.toml``, mode 0600,
which ``cylist setup`` writes for the CLI and both processes now share. The
registration Claude Code holds needs no ``env`` block at all, and moving the
server means editing one file rather than every client that points at it.

The environment still wins where it is set, so an MCP client that wants to be
explicit — a different server, a narrower token — says so and is obeyed.

The reader below is a copy of the CLI's, thirty lines of ``tomllib``. This
package deliberately depends on nothing of Cylist's; a shared distributable
to keep in step would cost more than the duplication does.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cylist_mcp.errors import CylistError

DEFAULT_URL = "http://localhost:8000"


def config_path() -> Path:
    """``~/.config/cylist/config.toml``, honouring ``XDG_CONFIG_HOME``."""
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "cylist" / "config.toml"


@dataclass(frozen=True)
class Settings:
    urls: tuple[str, ...]
    """Every address to try for the server, in order.

    More than one when the server has said it answers on more than one (see
    the backend's ``GET /setup``). A client that cannot connect to the first
    tries the next, which is what lets a laptop keep working when it leaves
    the network it was configured on.
    """

    token: str

    @property
    def url(self) -> str:
        """The first address, for the one line printed at startup."""
        return self.urls[0]


def load_settings() -> Settings:
    """Read the token and the addresses, failing loudly without a token.

    Failing at startup is deliberate: a server that starts without a token
    would advertise thirty tools that all return 401, and the model would
    spend a turn discovering that one at a time.
    """
    stored = _read(config_path())

    token = os.environ.get("CYLIST_TOKEN", "").strip() or _string(stored, "token")
    if not token:
        raise CylistError(
            "No Cylist token. Run 'cylist setup' on this machine, which mints one and "
            "writes it to ~/.config/cylist/config.toml — or set CYLIST_TOKEN in this "
            "server's env block. 'read,write' is enough to run a board.",
            code="unconfigured",
        )

    named = (os.environ.get("CYLIST_URL") or "").strip()
    if named:
        # Naming a server means that server, and nothing else.
        return Settings(urls=(named.rstrip("/"),), token=token)

    stored_url = _string(stored, "url")
    urls = _dedupe((stored_url or DEFAULT_URL, *_strings(stored, "urls")))
    return Settings(urls=urls, token=token)


def _read(path: Path) -> dict[str, Any]:
    """The config file, or nothing at all if it is absent or unreadable.

    Unreadable is not fatal: the environment may hold everything needed, and
    a stdio server has nowhere useful to complain to about a file it did not
    need.
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return {}
    try:
        return tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError):
        return {}


def _string(stored: dict[str, Any], key: str) -> str:
    value = stored.get(key)
    return value.strip() if isinstance(value, str) else ""


def _strings(stored: dict[str, Any], key: str) -> tuple[str, ...]:
    value = stored.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _dedupe(urls: tuple[str, ...]) -> tuple[str, ...]:
    """First appearance wins, order kept — the order is the order tried."""
    seen: dict[str, None] = {}
    for url in urls:
        cleaned = url.strip().rstrip("/")
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen) or (DEFAULT_URL,)
