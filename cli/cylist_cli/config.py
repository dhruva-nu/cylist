"""Where the CLI finds its server URL and its token.

Resolution order, first hit wins:

1. ``--url`` on the command line (URL only; there is deliberately no
   ``--token`` flag — see below).
2. ``CYLIST_URL`` / ``CYLIST_TOKEN`` in the environment.
3. ``~/.config/cylist/config.toml``, written by ``cylist setup`` or
   ``cylist login``.
4. ``http://localhost:8000`` for the URL; no token.

A stored configuration holds a *list* of addresses, not one, because one
server is often reachable several ways and which of them works depends on
where the laptop is rather than on anything either end decided at setup time.
``cylist setup`` asks the server for that list (``GET /setup``) and writes it
here; :mod:`cylist_cli.endpoints` decides the order they are tried in, and
:class:`cylist_cli.client.Client` moves down it when a connection cannot be
made. See :attr:`Config.urls`.

An address given explicitly — ``--url``, or ``CYLIST_URL`` — is the whole
list. Naming a server means that server: quietly reaching a different one
because the named one was down would be the opposite of what was asked.

There is no ``--token`` flag on purpose. A token passed as an argument is
copied into shell history, into ``ps`` output for as long as the process runs,
and into any shell trace the user has enabled. The environment variable and the
0600 config file are both better, and having no flag means no one reaches for
the worse option out of convenience.
"""

from __future__ import annotations

import os
import stat
import sys
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cylist_cli.errors import CylistError

DEFAULT_URL = "http://localhost:8000"

CONFIG_MODE = 0o600
"""Owner read/write only. The file holds a bearer token."""


def config_path() -> Path:
    """The config file's location, honouring ``XDG_CONFIG_HOME``."""
    root = os.environ.get("XDG_CONFIG_HOME")
    base = Path(root) if root else Path.home() / ".config"
    return base / "cylist" / "config.toml"


@dataclass(frozen=True)
class Config:
    """A resolved server URL and token, with where the token came from."""

    url: str
    token: str | None
    token_source: str
    urls: tuple[str, ...] = ()
    """Every address to try for this server, in order, :attr:`url` first.

    A single-entry list for a server named explicitly or reached one way.
    Longer when ``cylist setup`` has asked the server what else it answers on
    — which is what lets the same configuration work on and off a tailnet.
    """

    def __post_init__(self) -> None:
        if not self.urls:
            object.__setattr__(self, "urls", (self.url,))

    def require_token(self) -> str:
        """The token, or an error explaining the three ways to supply one."""
        if not self.token:
            raise CylistError(
                "No API token. Run 'cylist setup', or set CYLIST_TOKEN in the environment."
            )
        return self.token


def _read_file(path: Path) -> dict[str, Any]:
    """Read the config file, ignoring it if it is absent."""
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise CylistError(f"Cannot read {path}: {exc.strerror}.") from exc

    try:
        parsed = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise CylistError(f"{path} is not valid TOML: {exc}") from exc

    return parsed


def _string(stored: dict[str, Any], key: str) -> str | None:
    value = stored.get(key)
    return value if isinstance(value, str) else None


def _strings(stored: dict[str, Any], key: str) -> tuple[str, ...]:
    """A list-of-strings setting, ignoring anything that is not one.

    Hand-edited files happen, and a stray integer in ``urls`` should cost that
    entry rather than every command until somebody notices.
    """
    value = stored.get(key)
    if not isinstance(value, list):
        return ()
    return tuple(item.rstrip("/") for item in value if isinstance(item, str) and item.strip())


def load(url_override: str | None = None, *, path: Path | None = None) -> Config:
    """Resolve configuration from flag, environment and file, in that order."""
    where = path or config_path()
    stored = _read_file(where)

    named = url_override or os.environ.get("CYLIST_URL")
    stored_url = _string(stored, "url")
    url = (named or stored_url or DEFAULT_URL).rstrip("/")

    # A named server is the only candidate; a stored one carries whatever else
    # the server said it answers on, itself first.
    urls = (url,) if named else dedupe((url, *_strings(stored, "urls")))

    token = os.environ.get("CYLIST_TOKEN")
    source = "CYLIST_TOKEN"
    if not token:
        token = _string(stored, "token")
        source = str(where)
    if not token:
        source = "unset"

    return Config(url=url, token=token, token_source=source, urls=urls)


def dedupe(urls: Iterable[str]) -> tuple[str, ...]:
    """First appearance wins, order kept — the order is the whole point.

    Public because every producer of a candidate list needs it and they must
    all agree: an address that appears twice is an address tried twice, which
    for a timeout is twice the wait.
    """
    seen: dict[str, None] = {}
    for url in urls:
        cleaned = url.strip().rstrip("/")
        if cleaned:
            seen.setdefault(cleaned, None)
    return tuple(seen)


def save(url: str, token: str, *, urls: Iterable[str] = (), path: Path | None = None) -> Path:
    """Write the config file with mode 0600, and return where it went.

    The file is created 0600 rather than created and then chmod-ed: between
    those two calls a token would briefly be world-readable, and on a shared
    machine "briefly" is long enough.
    """
    where = path or config_path()
    candidates = dedupe((url, *urls))
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, CONFIG_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(_render(url, token, candidates))
        finally:
            # An existing file keeps its old mode through O_CREAT, so tighten
            # it as well — the token is new even when the file is not.
            os.chmod(where, CONFIG_MODE)  # noqa: PTH101 - path is already a Path
    except OSError as exc:
        raise CylistError(f"Cannot write {where}: {exc.strerror}.") from exc
    return where


def _render(url: str, token: str, urls: tuple[str, ...]) -> str:
    rendered = ", ".join(f'"{candidate}"' for candidate in urls)
    return (
        "# Written by 'cylist setup'. Mode 0600: this file holds a bearer token.\n"
        f'url = "{url}"\n'
        f"urls = [{rendered}]\n"
        f'token = "{token}"\n'
    )


def describe_mode(path: Path) -> str:
    """``0600``-style rendering of a file's permission bits.

    Reports what the filesystem says, which on Windows is ``0666`` whatever
    was asked for. Use :func:`describe_protection` to tell somebody their
    token is safe; this is the raw reading and not the reassurance.
    """
    return oct(stat.S_IMODE(path.stat().st_mode))[2:].rjust(4, "0")


def describe_protection(path: Path) -> str:
    """How this file is kept private, phrased truthfully for the platform.

    A mode on POSIX, because one was set and it holds. Not on Windows:
    ``os.chmod`` there moves the read-only bit and nothing else, so the file
    reads back ``0666`` however it was created, and "mode 0666 — owner
    read/write only" would be a reassurance that is not true. What actually
    protects it is the ACL Windows puts on the user's profile directory,
    which is where this file lives — the same thing pip and uv rely on for
    their own credentials.
    """
    if sys.platform == "win32":
        return "in your user profile, which only you and an administrator can read"
    else:
        return f"mode {describe_mode(path)} — owner read/write only"
