"""Where the CLI finds its server URL and its token.

Resolution order, first hit wins:

1. ``--url`` on the command line (URL only; there is deliberately no
   ``--token`` flag — see below).
2. ``CYLIST_URL`` / ``CYLIST_TOKEN`` in the environment.
3. ``~/.config/cylist/config.toml``, written by ``cylist login``.
4. ``http://localhost:8000`` for the URL; no token.

There is no ``--token`` flag on purpose. A token passed as an argument is
copied into shell history, into ``ps`` output for as long as the process runs,
and into any shell trace the user has enabled. The environment variable and the
0600 config file are both better, and having no flag means no one reaches for
the worse option out of convenience.
"""

from __future__ import annotations

import os
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path

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

    def require_token(self) -> str:
        """The token, or an error explaining the three ways to supply one."""
        if not self.token:
            raise CylistError(
                "No API token. Run 'cylist login', or set CYLIST_TOKEN in the environment."
            )
        return self.token


def _read_file(path: Path) -> dict[str, str]:
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

    return {key: value for key, value in parsed.items() if isinstance(value, str)}


def load(url_override: str | None = None, *, path: Path | None = None) -> Config:
    """Resolve configuration from flag, environment and file, in that order."""
    where = path or config_path()
    stored = _read_file(where)

    url = url_override or os.environ.get("CYLIST_URL") or stored.get("url") or DEFAULT_URL

    token = os.environ.get("CYLIST_TOKEN")
    source = "CYLIST_TOKEN"
    if not token:
        token = stored.get("token")
        source = str(where)
    if not token:
        source = "unset"

    return Config(url=url.rstrip("/"), token=token, token_source=source)


def save(url: str, token: str, *, path: Path | None = None) -> Path:
    """Write the config file with mode 0600, and return where it went.

    The file is created 0600 rather than created and then chmod-ed: between
    those two calls a token would briefly be world-readable, and on a shared
    machine "briefly" is long enough.
    """
    where = path or config_path()
    try:
        where.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(where, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, CONFIG_MODE)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                handle.write(_render(url, token))
        finally:
            # An existing file keeps its old mode through O_CREAT, so tighten
            # it as well — the token is new even when the file is not.
            os.chmod(where, CONFIG_MODE)  # noqa: PTH101 - path is already a Path
    except OSError as exc:
        raise CylistError(f"Cannot write {where}: {exc.strerror}.") from exc
    return where


def _render(url: str, token: str) -> str:
    return (
        "# Written by 'cylist login'. Mode 0600: this file holds a bearer token.\n"
        f'url = "{url}"\n'
        f'token = "{token}"\n'
    )


def describe_mode(path: Path) -> str:
    """``0600``-style rendering of a file's permission bits, for reassurance."""
    return oct(stat.S_IMODE(path.stat().st_mode))[2:].rjust(4, "0")
