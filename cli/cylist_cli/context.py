"""What every command handler is handed."""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx

from cylist_cli import endpoints
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

    def build_client(
        self,
        token: str | None,
        url: str | None = None,
        *,
        timeout: httpx.Timeout | None = None,
        budget: float | None = None,
    ) -> Client:
        """A client for an arbitrary token — used by ``login`` to check one, and
        by ``hook`` for one that must give up quickly.

        Given no ``url`` it takes every address the configuration holds, in the
        order :mod:`cylist_cli.endpoints` recommends, so a server that answers
        on more than one is found wherever the machine happens to be. Given
        one, that address is the only one tried.
        """
        urls = [url] if url else list(endpoints.order(self.config.urls))
        return Client(urls, token, transport=self.transport, timeout=timeout, budget=budget)

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
