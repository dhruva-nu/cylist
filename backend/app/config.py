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
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError

Environment = Literal["dev", "test", "preview", "staging", "prod"]

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]
"""Named rather than free text so a typo is refused at startup.

``CYLIST_LOG_LEVEL=INFOO`` would otherwise be read by :mod:`logging` as level
zero and turn every logger all the way up, which is the opposite of what
whoever typed it meant.
"""

LogFormat = Literal["text", "json"]
"""``text`` for a person reading a terminal, ``json`` for anything parsing.

One object per line, so ``jq`` and any log shipper can read the file without a
multi-line grammar for tracebacks.
"""

AGENT_SOCKET_IDLE_AFTER = timedelta(minutes=5)
"""How long an agent's socket may say nothing before the server closes it.

Measured on *application* messages. Protocol pongs are answered below the
ASGI layer and never reach the handler, which is what makes this mean "the
agent has nothing to say" rather than "the TCP connection is quiet".

The client's side of the bargain: it keeps the socket warm while it is
working — a single tool call can run for twenty minutes without a hook event
— and lets it go quiet once it is waiting on a human. So this window closing
means one of two things, and the board draws them the same way: the agent is
gone, or the person is.

Comfortably more than the client's sixty-second keepalive, so five missed in
a row is the threshold rather than one unlucky one.
"""

AGENT_QUIET_AFTER = timedelta(minutes=10)
"""How long an *unwitnessed* session may go quiet before it is ended.

For the clients that report over HTTP and hold no socket. A socket closing
says the session is over; a PUT that stops coming says nothing at all, so
something has to go looking. A session holding a socket is exempt however
long it has been silent — it is witnessed, which is better evidence than a
timestamp.

This is what the computed staleness used to do, moved from the read to a
write: the board now reads a fact rather than a guess about the clock.
"""

REAP_EVERY = timedelta(seconds=60)
"""How often to go looking. Cheap — one indexed query over the open rows."""


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

    # --- Logging ----------------------------------------------------------
    # What the process says about itself, and where. See app/core/logging.py
    # for what is written; these decide how much of it and to where.
    log_level: LogLevel = "INFO"
    log_format: LogFormat = "text"

    log_to_file: bool = False
    """Whether to also write the log to a file on the server.

    Off by default because the log's first home is stderr, which is where a
    container's output belongs and what ``make prod-logs`` follows. Turning
    this on *adds* a file; it never silences stderr, so switching it on can
    never make a deployment quieter than it was.
    """

    log_file: Path | None = None
    """Where that file goes, when :attr:`log_to_file` is set.

    Left unset it is :attr:`log_dir` ``/cylist.log``, which is inside the data
    directory and therefore inside the one volume every deployment already
    mounts and backs up — so enabling the file needs one variable rather than
    a variable and a mount.
    """

    log_file_max_mb: int = Field(default=10, gt=0)
    log_file_backups: int = Field(default=5, ge=0)
    """How much log to keep: ``log_file_backups`` rolled files of
    ``log_file_max_mb`` each, plus the live one. Rotation is not optional —
    an unbounded log file on a server is a disk that fills up quietly and
    takes Postgres down with it, which is a far worse outage than the missing
    logs it was meant to prevent.
    """

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
    def log_dir(self) -> Path:
        """Where log files live when they are written at all."""
        return self.data_dir / "logs"

    @property
    def log_file_path(self) -> Path:
        """The file the log is written to, configured or derived."""
        return self.log_file if self.log_file is not None else self.log_dir / "cylist.log"

    @property
    def log_file_max_bytes(self) -> int:
        return self.log_file_max_mb * 1024 * 1024

    @property
    def safe_database_url(self) -> str:
        """The database URL with its password blanked out, for logging.

        The configured URL carries the password in it, so it can never be
        written to a log or shown on a status page as it stands. What is left
        after redaction — driver, host, port, database — is exactly the part
        worth seeing when a deployment has come up pointed at the wrong one.
        """
        try:
            return make_url(self.database_url).render_as_string(hide_password=True)
        except ArgumentError:
            # Unparseable, so nothing can be said about its shape safely.
            return "<unparseable database url>"

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
