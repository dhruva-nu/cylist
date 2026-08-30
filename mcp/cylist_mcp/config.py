"""Configuration, from the environment only.

An MCP server is started by its client — Claude Code, an IDE, an agent runtime
— with no terminal to prompt at, so there is no config file and no login flow
here. ``CYLIST_URL`` and ``CYLIST_TOKEN`` come from the ``env`` block of the
MCP client's configuration; see the README.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from cylist_mcp.errors import CylistError

DEFAULT_URL = "http://localhost:8000"


@dataclass(frozen=True)
class Settings:
    url: str
    token: str


def from_environment() -> Settings:
    """Read the two variables, failing loudly if the token is absent.

    Failing at startup is deliberate: a server that starts without a token
    would advertise fourteen tools that all return 401, and the model would
    spend a turn discovering that one at a time.
    """
    token = os.environ.get("CYLIST_TOKEN", "").strip()
    if not token:
        raise CylistError(
            "CYLIST_TOKEN is not set. Mint one with POST /tokens and put it in the "
            "MCP server's env block — 'read,write' is enough to run a board.",
            code="unconfigured",
        )
    url = (os.environ.get("CYLIST_URL") or DEFAULT_URL).rstrip("/")
    return Settings(url=url, token=token)
