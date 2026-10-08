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
from pydantic import AliasChoices, Field, field_validator
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

The client's side of the bargain: it keeps the socket warm for as long as the
session is open, working or waiting alike — a single tool call can run for
twenty minutes without a hook event, and a person can leave a prompt
unanswered for an afternoon. So this window closing means one thing only:
*the daemon has gone*. It is not how the board learns that a human has
wandered off, and it never was a good way to.

It used to be read as both. The client stopped its keepalive while waiting,
so a session waiting on its human went quiet, was closed here at five
minutes, and had its card put out while the conversation was still on screen
— nine cards in ten, on a machine running ten sessions. The client now ends a
session when the ``claude`` process it belongs to exits (CYLIST-74), which is
the question actually being asked, and says ``bye`` on its way out. Nothing
here changed; it simply stopped being asked to guess.

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
    fail loudly rather than silently accepting everything — an empty
    ``password_hash`` refuses every bootstrap login rather than accepting
    every one.
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
    """Argon2 hash of the *bootstrap* password — see :mod:`app.routers.auth`.

    Not anybody's password. It is accepted at ``POST /auth/login`` only while
    no person in the directory has one of their own, which makes it the way a
    fresh deployment is opened and nothing else. Everyday passwords live on
    ``person.password_hash`` and never pass through configuration.
    """

    vault_key: str = ""

    # --- Jev ----------------------------------------------------------------
    # TypeSafe's System One model, which jev-docs asks which section of a
    # project's docs answers an agent's question, and where a new fact belongs
    # — see app/services/doc_engine.py. Here rather than in the MCP server, so
    # the key lives in one place and every client gets the same answer.
    jev_api_key: str = Field(
        default="", validation_alias=AliasChoices("CYLIST_JEV_API_KEY", "JEV_API_KEY")
    )
    """Read as ``JEV_API_KEY``, the name the SDK itself uses, as well as the
    prefixed one. Empty means jev is not asked at all: every question comes
    back unanswered, so the agent reads the code, and a doc written without a
    topic is refused with the topics to choose from — the same answers as when
    jev is down."""

    jev_model: str = "jev-latest"

    jev_timeout_seconds: float = Field(default=10.0, gt=0)
    """Per request; a question is about five of them. Short, because a question
    sits in front of an agent's work, and reading the code is a better answer
    than a long wait."""

    doc_relevance_threshold: float = Field(default=0.7, ge=0, le=1)
    """How likely jev must think a section answers the question, having read it,
    before the answer is ``ok`` rather than ``unverified``. jev-docs' calibrated
    cut: its correct routes score 0.86 and up, its wrong ones 0.5 to 0.65."""

    # --- HTTP -------------------------------------------------------------
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])
    session_ttl_hours: int = Field(default=720, gt=0)

    client_urls: list[str] = Field(default_factory=list)
    """Every address this deployment can be reached at, most local first.

    Handed to clients by ``GET /setup`` so that a CLI or an MCP server keeps
    the whole list and tries them in order. One address is enough for a server
    reached one way; a machine on a tailnet with a public funnel in front of
    it has three, and which of them works depends on where the laptop is
    sitting at the time rather than on anything either end can decide once.

    Order is the order a client will try them in, so put the cheapest first:
    ``["http://localhost:8000", "https://box.tailnet.ts.net"]``.
    """

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
    def is_deployed(self) -> bool:
        """Whether this is a deployed stack rather than someone's machine.

        Preview, staging and production differ in which data they hold, not in
        how they are run: all three are one container behind ``tailscale
        serve``, reached over HTTPS, holding rows someone would miss. The
        decisions that turn on that — issuing the session cookie ``Secure``,
        refusing to seed fictional data over the top — belong here.

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
