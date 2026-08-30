"""What every command handler is handed."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from cylist_cli.client import Client
from cylist_cli.config import Config


@dataclass
class Context:
    """Configuration, the ``--json`` flag, and a lazily built HTTP client.

    Lazy because ``cylist login`` has to run before there is a token to build
    a client with, and because ``--help`` should never open a socket.
    """

    config: Config
    as_json: bool = False
    transport: httpx.BaseTransport | None = None
    _client: Client | None = field(default=None, repr=False)

    @property
    def client(self) -> Client:
        if self._client is None:
            self._client = self.build_client(self.config.require_token())
        return self._client

    def build_client(self, token: str, url: str | None = None) -> Client:
        """A client for an arbitrary token — used by ``login`` to check one."""
        return Client(url or self.config.url, token, transport=self.transport)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
