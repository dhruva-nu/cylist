"""What the process says about itself, and where it says it.

Cylist keeps two records and they are deliberately not the same one. The
``activity`` table is what *people and agents did* — it is rows, it is
queried, paginated and drawn on screens, and it is the thing to read when the
question is who moved a card. This module is the record of what the *process*
did: requests served, credentials refused, blobs written, exceptions nobody
caught. Keeping them apart is what stops the log turning into a second, worse
audit trail — one nobody can query and everybody has to grep — so a domain
event that already lands in ``activity`` is not logged again here just because
it could be.

Where it goes
-------------

Always to stderr, which is where a container's output belongs: ``make
prod-logs`` follows it, Docker rotates it, and nothing has to be mounted for
it to work. Setting ``CYLIST_LOG_TO_FILE=true`` *adds* a rotating file under
the data directory — the one volume every deployment already mounts and backs
up — so keeping logs across a container restart costs one variable and no new
plumbing. It is an addition, never a redirection: switching it on cannot make
a deployment quieter than it was.

Correlating lines
-----------------

Every line carries the id of the request that produced it, held in a
:class:`~contextvars.ContextVar` so a service five calls deep does not have to
be handed one. That is the difference between a log you can read and a log you
can only stare at: with one 12-character id, the access line, the warning from
the service and the traceback underneath it are provably the same request,
even with a dozen in flight. The id also goes out on the response as
``X-Request-ID``, so a failure a user reports can be found without guessing at
timestamps.

What is never written
---------------------

Credentials, in any form: no ``Authorization`` header, no cookie, no token —
not even a hash, which is exactly the string the database compares against.
No request or response bodies, which is where passwords and vault secrets
live. No query strings, because Cylist's own are user content (``?q=`` is
whatever somebody searched for) and because a query string is where
credentials most often end up by accident in systems that log them. A log that
has to be treated as a secret is a log nobody can paste into a bug report.
"""

from __future__ import annotations

import json
import logging
import logging.handlers
import sys
import uuid
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Final

if TYPE_CHECKING:  # pragma: no cover - import cycle, and only needed for types
    from app.config import Settings

REQUEST_ID_HEADER: Final = "X-Request-ID"
"""The response header carrying the request id, and the request header an
already-correlated caller may set to keep its own."""

CONTEXT_KEY: Final = "context"
"""The ``extra=`` key that carries structured fields.

::

    logger.info("Upload stored", extra={"context": {"bytes": size}})

One reserved name rather than arbitrary ``extra`` keys, because arbitrary keys
collide with :class:`logging.LogRecord`'s own attributes — ``message``,
``module``, ``name`` and ``filename`` among them — and the collision is an
exception raised from inside the logging call, at the moment something has
already gone wrong enough to be worth logging.
"""

REQUEST_ID_LENGTH: Final = 12
"""Hex characters of request id. 48 bits: long enough that two ids colliding
within one log file is not a thing that happens, short enough to read out over
a call and to leave the line legible."""

_HANDLER_TAG: Final = "_cylist_handler"
"""Marks the handlers this module installed, so reconfiguring replaces them
and leaves anything else — ``pytest``'s capture, an embedding process's own —
exactly where it was."""

_request_id: ContextVar[str | None] = ContextVar("cylist_request_id", default=None)

logger = logging.getLogger(__name__)


# --- Request correlation ---------------------------------------------------


def new_request_id() -> str:
    """Mint an id for a request that arrived without one."""
    return uuid.uuid4().hex[:REQUEST_ID_LENGTH]


def current_request_id() -> str | None:
    """The request being served on this task, if there is one."""
    return _request_id.get()


def bind_request_id(request_id: str) -> Token[str | None]:
    """Attach an id to this task; pass the token back to :func:`reset_request_id`."""
    return _request_id.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Undo a :func:`bind_request_id`.

    Reset rather than set-to-``None``: a context variable left holding the
    last request's id would quietly stamp that id on anything logged by
    background work afterwards, which is worse than no id at all.
    """
    _request_id.reset(token)


def sanitise_request_id(value: str | None) -> str | None:
    """Accept a caller's own request id, or reject it.

    An inbound header is attacker-controlled and goes straight into a log
    line, so a newline in it would let anyone reaching the API forge entries —
    the classic log injection. Only printable ASCII survives, capped at a
    length that cannot push the real content of a line off a screen.
    """
    if not value:
        return None
    candidate = value.strip()
    if not candidate or len(candidate) > 64:
        return None
    if not all(" " <= character <= "~" for character in candidate):
        return None
    return candidate


class RequestIdFilter(logging.Filter):
    """Give every record a ``request_id``, even the ones raised outside one.

    A filter rather than a formatter concern: the attribute has to exist by
    the time *any* formatter runs, including one somebody else installed.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "request_id"):
            record.request_id = _request_id.get()
        return True


# --- Formatters ------------------------------------------------------------


def _record_context(record: logging.LogRecord) -> dict[str, Any]:
    context = getattr(record, CONTEXT_KEY, None)
    return context if isinstance(context, dict) else {}


def _timestamp(record: logging.LogRecord) -> str:
    """ISO 8601 in UTC, to the millisecond.

    UTC and not the server's zone: the deployments, the browser and whoever is
    reading are not reliably in the same one, and a log that needs a timezone
    lookup before two lines can be ordered is a log that gets misread.
    """
    moment = datetime.fromtimestamp(record.created, tz=UTC)
    return f"{moment.strftime('%Y-%m-%dT%H:%M:%S')}.{moment.microsecond // 1000:03d}Z"


class TextFormatter(logging.Formatter):
    """One line per record, for a person reading a terminal.

    Fixed-width level and logger name so the messages line up in a column and
    the eye can run down them; structured context trails as ``key=value``
    pairs, which stays greppable without pretending to be a data format.
    """

    def format(self, record: logging.LogRecord) -> str:
        request_id = getattr(record, "request_id", None) or "-"
        line = (
            f"{_timestamp(record)} {record.levelname:<8} "
            f"{record.name:<26} [{request_id}] {record.getMessage()}"
        )

        context = _record_context(record)
        if context:
            line += "  " + " ".join(f"{key}={_render(value)}" for key, value in context.items())

        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        if record.stack_info:
            line += "\n" + self.formatStack(record.stack_info)
        return line


def _render(value: Any) -> str:
    """Render one context value so a space in it cannot fake a second pair."""
    text = str(value)
    return json.dumps(text) if any(character.isspace() for character in text) else text


class JsonFormatter(logging.Formatter):
    """One JSON object per line, for anything that parses rather than reads.

    The traceback is a string field rather than nested structure, which keeps
    a multi-line exception inside a single line of the file — the property
    that lets a shipper treat the file as newline-delimited JSON and not need
    a grammar for continuations.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "time": _timestamp(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        request_id = getattr(record, "request_id", None)
        if request_id:
            payload["request_id"] = request_id

        context = _record_context(record)
        if context:
            payload["context"] = context

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        # default=str so an unexpected value — a Path, a UUID, a datetime —
        # degrades to its repr instead of losing the whole line to a
        # TypeError raised from inside the logging call.
        return json.dumps(payload, default=str)


def _formatter(log_format: str) -> logging.Formatter:
    return JsonFormatter() if log_format == "json" else TextFormatter()


# --- Configuration ---------------------------------------------------------


def configure_logging(settings: Settings) -> None:
    """Install Cylist's handlers on the root logger.

    Idempotent, and it has to be: the app factory calls it, and the test suite
    builds a great many apps. Only the handlers this module installed before
    are removed, so ``pytest``'s capture and anything an embedding process
    attached survive being reconfigured around.

    Called from :func:`app.main.create_app` rather than at import time, so the
    configuration it reads is the one the app was actually built with — a test
    app pointed at its own settings gets its own logging, not the
    environment's.
    """
    root = logging.getLogger()
    _remove_our_handlers(root)

    level = logging.getLevelNamesMapping()[settings.log_level]
    root.setLevel(level)

    formatter = _formatter(settings.log_format)
    _install(root, _stream_handler(formatter, level))

    if settings.log_to_file:
        handler = _file_handler(settings, formatter, level)
        if handler is not None:
            _install(root, handler)

    _quieten_libraries(settings)


def _install(root: logging.Logger, handler: logging.Handler) -> None:
    setattr(handler, _HANDLER_TAG, True)
    handler.addFilter(RequestIdFilter())
    root.addHandler(handler)


def _remove_our_handlers(root: logging.Logger) -> None:
    for handler in [h for h in root.handlers if getattr(h, _HANDLER_TAG, False)]:
        root.removeHandler(handler)
        handler.close()


class StderrHandler(logging.StreamHandler):  # type: ignore[type-arg]
    """Writes to whatever ``sys.stderr`` is *now*.

    :class:`logging.StreamHandler` binds the stream object it is handed once,
    at construction. That is wrong for a handler installed this early: pytest
    replaces ``sys.stderr`` for the duration of a test and closes the
    replacement afterwards, and ``uvicorn --reload`` swaps it too — after
    which every record goes to a closed file and logging starts printing
    "I/O operation on closed file" instead of the line. Resolving the stream
    per record costs an attribute lookup and removes the whole class of
    problem.
    """

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property
    def stream(self) -> Any:
        return sys.stderr

    @stream.setter
    def stream(self, _value: object) -> None:
        """Swallow the base class's assignment in ``__init__``."""


def _stream_handler(formatter: logging.Formatter, level: int) -> logging.Handler:
    """The always-present handler, on stderr.

    stderr rather than stdout because that is where uvicorn already writes,
    so the two halves of a container's output stay in one stream and in
    order — interleaving them across two pipes reorders lines under load.
    """
    handler = StderrHandler()
    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def _file_handler(
    settings: Settings, formatter: logging.Formatter, level: int
) -> logging.Handler | None:
    """The rotating file, when one is asked for.

    Returns ``None`` — having complained to stderr — when the file cannot be
    opened. A read-only volume or a directory owned by another user is a
    misconfiguration worth shouting about, but it is not a reason to refuse to
    start: the alternative is a deployment that comes up fine, has file
    logging switched on later, and then will not boot at all.
    """
    path = settings.log_file_path
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.handlers.RotatingFileHandler(
            path,
            maxBytes=settings.log_file_max_bytes,
            backupCount=settings.log_file_backups,
            encoding="utf-8",
            # Opened on the first record rather than here, so a process that
            # never logs anything does not leave an empty file behind.
            delay=True,
        )
    except OSError as error:
        # print, not logging: logging is precisely what is not working yet.
        print(
            f"cylist: cannot open the log file at {path} ({error}); continuing with stderr only.",
            file=sys.stderr,
        )
        return None

    handler.setFormatter(formatter)
    handler.setLevel(level)
    return handler


def _quieten_libraries(settings: Settings) -> None:
    """Stop the libraries logging the same events in their own shapes.

    uvicorn installs handlers of its own on ``uvicorn``, ``uvicorn.error`` and
    ``uvicorn.access`` and marks them non-propagating, which would print every
    line twice in two different formats and put none of it in the file.
    Clearing the handlers and letting the records propagate puts everything
    uvicorn has to say through the formatter and into the same places.

    ``uvicorn.access`` is turned off rather than reformatted:
    :class:`app.core.request_log.RequestLogMiddleware` already logs one line
    per request, with the id, the duration and the level graded by status —
    everything uvicorn's version has and more.
    """
    for name in ("uvicorn", "uvicorn.error", "fastapi"):
        library = logging.getLogger(name)
        library.handlers.clear()
        library.propagate = True

    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.disabled = True

    # SQLAlchemy's statement log is deafening and already has a switch of its
    # own; honour that one rather than inventing a second.
    logging.getLogger("sqlalchemy.engine").setLevel(
        logging.INFO if settings.database_echo else logging.WARNING
    )


def describe_destination(settings: Settings) -> str:
    """Where this process's log is going, in words, for the startup line."""
    if not settings.log_to_file:
        return "stderr"
    return f"stderr and {settings.log_file_path}"
