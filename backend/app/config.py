"""Application settings, loaded once from the environment.

Every setting is prefixed with ``CYLIST_`` so the process environment stays
readable next to unrelated variables. See ``.env.example`` for the full list.
"""

from __future__ import annotations

from datetime import timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal

from fastapi import Request
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

Environment = Literal["dev", "test", "preview", "staging", "prod"]

AGENT_SESSION_STALE_AFTER = timedelta(minutes=10)
"""How long a working agent session may go unheard from before the board stops
believing it.

A Claude Code session reports in on every prompt and tool call, and at least
once a minute while a long turn runs; one silent for ten minutes has almost
certainly been killed without its ``SessionEnd`` hook firing. The row stays
``working`` in the database — nothing reaps it — and the read model calls it
stale instead, so a crashed agent never pulses on a card forever. A constant
rather than a setting: it is a fact about the hook's cadence, not about the
deployment.
"""


class Settings(BaseSettings):
    """Runtime configuration.

    Secrets (``password_hash``, ``vault_key``) default to empty so the app can
    boot in development before they are generated; the endpoints that need them
    fail loudly rather than silently accepting everything.
    """

    model_config = SettingsConfigDict(
        env_prefix="CYLIST_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = "dev"

    # --- Database ---------------------------------------------------------
    database_url: str = "postgresql+asyncpg://cylist:cylist@localhost:5432/cylist"
    database_echo: bool = False

    # --- Storage ----------------------------------------------------------
    data_dir: Path = Path("./data")
    max_upload_mb: int = Field(default=200, gt=0)

    # --- The web app ------------------------------------------------------
    # Where the built SPA lives, relative to the working directory. The
    # production image builds it here; in development it does not exist and the
    # app is served by Vite instead, so the default is simply never found.
    web_dir: Path = Path("./web")

    # --- Secrets ----------------------------------------------------------
    password_hash: str = ""
    vault_key: str = ""

    # --- HTTP -------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_ttl_hours: int = Field(default=720, gt=0)

    @field_validator("database_url")
    @classmethod
    def _require_async_driver(cls, value: str) -> str:
        """Guard against the sync driver, which would block the event loop."""
        if not value.startswith("postgresql+asyncpg://"):
            raise ValueError("database_url must use the postgresql+asyncpg:// driver")
        return value

    @property
    def blob_dir(self) -> Path:
        """Where uploaded file contents live, addressed by digest."""
        return self.data_dir / "blobs"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment == "prod"

    @property
    def is_deployed(self) -> bool:
        """Whether this is a deployed stack rather than someone's machine.

        Preview, staging and production differ in which data they hold, not in
        how they are run: all three are one container behind ``tailscale
        serve``, reached over HTTPS, holding rows someone would miss. The
        decisions that turn on that — issuing the session cookie ``Secure``,
        refusing to seed fictional data over the top — belong here rather than
        on :attr:`is_production`, which stays a question about which stack this
        is.

        ``"dev"`` stays out of this set on purpose: it is the default for
        someone's own machine, where neither of those protections should apply.
        The environment this property calls "preview" is a real deployment
        (see DEPLOY.md's Dev section) — it is just not called ``"dev"`` in
        ``CYLIST_ENVIRONMENT``, to avoid exactly this collision.
        """
        return self.environment in ("preview", "staging", "prod")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings, read from the environment once."""
    return Settings()


def app_settings(request: Request) -> Settings:
    """FastAPI dependency returning the settings this app was built with.

    Routes must depend on this rather than on :func:`get_settings`, whose cache
    is process-wide: a test that builds an app with its own configuration has
    to see that configuration, not whatever is in the environment.
    """
    settings: Settings = request.app.state.settings
    return settings
