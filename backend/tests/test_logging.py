"""Logging: what is written, where it goes, and what must never appear in it.

The tests that matter most here are the negative ones. A formatter that drops
a field is a nuisance; a log that quietly grows a copy of the session cookie
in it is a security bug that nobody notices until the file is attached to a
bug report, so those are asserted directly rather than left to review.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest
from httpx import AsyncClient

from app.config import Settings
from app.core.logging import (
    REQUEST_ID_HEADER,
    JsonFormatter,
    TextFormatter,
    configure_logging,
    current_request_id,
    sanitise_request_id,
)
from app.db import Database
from tests.conftest import OWNER_PASSWORD, client_for


@pytest.fixture(autouse=True)
def restore_root_logger() -> Iterator[None]:
    """Put the root logger back exactly as it was after every test here.

    These tests reconfigure logging for the whole process, which is the only
    way to test something that is by nature global. Without this the first one
    to enable a file would leave every later test in the run writing into a
    ``tmp_path`` that pytest has already deleted.
    """
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    try:
        yield
    finally:
        for handler in list(root.handlers):
            if handler not in handlers:
                root.removeHandler(handler)
                handler.close()
        for handler in handlers:
            if handler not in root.handlers:
                root.addHandler(handler)
        root.setLevel(level)


def record(
    message: str = "hello",
    *,
    level: int = logging.INFO,
    request_id: str | None = None,
    context: dict[str, object] | None = None,
) -> logging.LogRecord:
    """A log record shaped like the ones the app produces."""
    made = logging.LogRecord("app.test", level, "test.py", 1, message, None, None)
    made.request_id = request_id  # type: ignore[attr-defined]
    if context is not None:
        made.context = context  # type: ignore[attr-defined]
    return made


# --- Formatters ------------------------------------------------------------


def test_text_format_carries_level_logger_and_message() -> None:
    line = TextFormatter().format(record("Signed in", request_id="abc123"))
    assert "INFO" in line
    assert "app.test" in line
    assert "[abc123]" in line
    assert line.endswith("Signed in")


def test_text_format_marks_a_record_that_belongs_to_no_request() -> None:
    """Startup and shutdown lines have no request; they must still be readable."""
    assert "[-]" in TextFormatter().format(record("Cylist starting"))


def test_text_format_quotes_a_context_value_containing_spaces() -> None:
    """Otherwise a space inside a value reads as the start of the next pair."""
    line = TextFormatter().format(record(context={"label": "My laptop", "kind": "session"}))
    assert 'label="My laptop"' in line
    assert "kind=session" in line


def test_json_format_is_one_parseable_object_per_line() -> None:
    line = JsonFormatter().format(
        record("Signed in", request_id="abc123", context={"token_id": "t-1"})
    )
    assert "\n" not in line
    payload = json.loads(line)
    assert payload["level"] == "INFO"
    assert payload["logger"] == "app.test"
    assert payload["message"] == "Signed in"
    assert payload["request_id"] == "abc123"
    assert payload["context"] == {"token_id": "t-1"}


def test_json_format_keeps_a_traceback_on_one_line() -> None:
    """The property a shipper relies on to treat the file as newline-delimited."""
    try:
        raise ValueError("boom")
    except ValueError:
        made = record("Request failed", level=logging.ERROR)
        made.exc_info = sys.exc_info()

    line = JsonFormatter().format(made)
    assert "\n" not in line
    payload = json.loads(line)
    assert "ValueError: boom" in payload["exception"]


def test_json_format_survives_a_context_value_it_cannot_serialise() -> None:
    """A Path or a UUID in the context must not cost the whole line."""
    line = JsonFormatter().format(record(context={"data_dir": Path("/data")}))
    assert json.loads(line)["context"]["data_dir"] == "/data"


# --- Inbound request ids ---------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "trace-abc-123",
        "0123456789abcdef",
    ],
)
def test_a_sane_inbound_request_id_is_adopted(value: str) -> None:
    assert sanitise_request_id(value) == value


@pytest.mark.parametrize(
    ("value", "why"),
    [
        (None, "absent"),
        ("", "empty"),
        ("   ", "whitespace only"),
        ("a" * 65, "too long to keep the line legible"),
        ("abc\ndef", "a newline would let a caller forge a second log line"),
        ("abc\r\nWARNING forged", "the same, spelled with a carriage return"),
        ("abc\x00def", "a control character"),
    ],
)
def test_an_unusable_inbound_request_id_is_refused(value: str | None, why: str) -> None:
    assert sanitise_request_id(value) is None, why


# --- Configuration ---------------------------------------------------------


def settings_for(tmp_path: Path, **overrides: object) -> Settings:
    return Settings(
        environment="test",
        data_dir=tmp_path,
        cors_origins=[],
        **overrides,  # type: ignore[arg-type]
    )


def test_no_file_is_written_unless_one_is_asked_for(tmp_path: Path) -> None:
    configure_logging(settings_for(tmp_path))
    logging.getLogger("app.test").info("hello")
    assert not (tmp_path / "logs").exists()


def test_enabling_the_file_writes_the_log_under_the_data_directory(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, log_to_file=True)
    configure_logging(settings)
    logging.getLogger("app.test").info("a line for the file")

    written = settings.log_file_path.read_text(encoding="utf-8")
    assert settings.log_file_path == tmp_path / "logs" / "cylist.log"
    assert "a line for the file" in written


def test_the_file_can_be_put_somewhere_else(tmp_path: Path) -> None:
    elsewhere = tmp_path / "somewhere" / "cylist.log"
    settings = settings_for(tmp_path, log_to_file=True, log_file=elsewhere)
    configure_logging(settings)
    logging.getLogger("app.test").info("over here")

    assert "over here" in elsewhere.read_text(encoding="utf-8")


def test_the_file_is_json_when_that_is_the_format(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, log_to_file=True, log_format="json")
    configure_logging(settings)
    logging.getLogger("app.test").info("structured", extra={"context": {"n": 1}})

    lines = settings.log_file_path.read_text(encoding="utf-8").strip().splitlines()
    assert json.loads(lines[-1])["context"] == {"n": 1}


def test_the_level_is_the_configured_one(tmp_path: Path) -> None:
    settings = settings_for(tmp_path, log_to_file=True, log_level="WARNING")
    configure_logging(settings)
    logging.getLogger("app.test").info("beneath the threshold")
    logging.getLogger("app.test").warning("above it")

    written = settings.log_file_path.read_text(encoding="utf-8")
    assert "beneath the threshold" not in written
    assert "above it" in written


def test_reconfiguring_does_not_stack_up_handlers(tmp_path: Path) -> None:
    """The app factory calls this, and the suite builds a great many apps."""
    settings = settings_for(tmp_path, log_to_file=True)
    for _ in range(3):
        configure_logging(settings)
    logging.getLogger("app.test").info("said once")

    written = settings.log_file_path.read_text(encoding="utf-8")
    assert written.count("said once") == 1


def test_a_handler_somebody_else_installed_is_left_alone(tmp_path: Path) -> None:
    """Only our own handlers are swept away — pytest's capture must survive."""
    theirs = logging.NullHandler()
    root = logging.getLogger()
    root.addHandler(theirs)
    try:
        configure_logging(settings_for(tmp_path))
        assert theirs in root.handlers
    finally:
        root.removeHandler(theirs)


def test_an_unwritable_log_file_does_not_stop_the_app(tmp_path: Path) -> None:
    """A misconfigured path is worth complaining about, not worth refusing to boot."""
    blocked = tmp_path / "a-file"
    blocked.write_text("not a directory", encoding="utf-8")

    settings = settings_for(tmp_path, log_to_file=True, log_file=blocked / "logs" / "cylist.log")
    configure_logging(settings)  # must not raise
    logging.getLogger("app.test").info("still logging to stderr")


# --- Redaction -------------------------------------------------------------


def test_the_database_password_is_not_in_the_url_that_gets_logged(tmp_path: Path) -> None:
    settings = settings_for(
        tmp_path,
        database_url="postgresql+asyncpg://cylist:hunter2@db.internal:5432/cylist",
    )
    safe = settings.safe_database_url
    assert "hunter2" not in safe
    # Everything worth seeing is still there.
    assert "db.internal" in safe
    assert "5432" in safe
    assert "cylist" in safe


# --- Through the app -------------------------------------------------------


def written_lines(path: Path) -> list[dict[str, object]]:
    """Every JSON record in a log file."""
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def access_lines(path: Path) -> list[dict[str, object]]:
    """Only the per-request lines this application wrote.

    The suite drives the app through ``httpx``, whose *client* logs a line per
    request of its own — including the full URL, query string and all. That
    is the test harness talking, not Cylist, so filtering to our own logger is
    what keeps these tests about the code under test.
    """
    return [line for line in written_lines(path) if line["logger"] == "app.request"]


async def test_every_response_carries_a_request_id(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.headers[REQUEST_ID_HEADER]


async def test_a_caller_that_brings_its_own_request_id_keeps_it(client: AsyncClient) -> None:
    """So a CLI or MCP server can tie its own logs to the server's."""
    response = await client.get("/health", headers={REQUEST_ID_HEADER: "cli-run-7"})
    assert response.headers[REQUEST_ID_HEADER] == "cli-run-7"


async def test_a_forged_request_id_is_replaced_rather_than_echoed(client: AsyncClient) -> None:
    response = await client.get("/health", headers={REQUEST_ID_HEADER: "a" * 200})
    returned = response.headers[REQUEST_ID_HEADER]
    assert returned != "a" * 200
    assert len(returned) == 12


async def test_the_id_is_not_left_bound_after_the_request(client: AsyncClient) -> None:
    """A leaked id would stamp the last request onto unrelated later lines."""
    await client.get("/health")
    assert current_request_id() is None


async def test_each_request_gets_its_own_id(client: AsyncClient) -> None:
    first = await client.get("/health")
    second = await client.get("/health")
    assert first.headers[REQUEST_ID_HEADER] != second.headers[REQUEST_ID_HEADER]


async def test_a_request_is_logged_with_its_method_path_status_and_duration(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    configured = settings.model_copy(
        update={"log_to_file": True, "log_file": tmp_path / "requests.log", "log_format": "json"}
    )
    async with client_for(configured, database) as http:
        await http.get("/me")  # 401: unauthenticated, and logged as such

    access = access_lines(tmp_path / "requests.log")
    assert len(access) == 1
    context = access[0]["context"]
    assert context["method"] == "GET"
    assert context["path"] == "/api/v1/me"
    assert context["status"] == 401
    assert context["duration_ms"] >= 0


async def test_a_refused_request_is_a_warning_and_a_served_one_is_not(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    """The grading is the thing that makes `grep WARNING` worth typing."""
    configured = settings.model_copy(
        update={"log_to_file": True, "log_file": tmp_path / "levels.log", "log_format": "json"}
    )
    async with client_for(configured, database) as http:
        refused = await http.get("/me")  # no credential
        assert refused.status_code == 401
        served = await http.post("/auth/login", json={"password": OWNER_PASSWORD})
        assert served.status_code == 200

    by_path = {
        line["context"]["path"]: line["level"] for line in access_lines(tmp_path / "levels.log")
    }
    assert by_path["/api/v1/me"] == "WARNING"
    assert by_path["/api/v1/auth/login"] == "INFO"


async def test_health_checks_do_not_fill_the_log(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    """Docker hits /health every thirty seconds; at INFO that is all anyone would see."""
    configured = settings.model_copy(
        update={"log_to_file": True, "log_file": tmp_path / "health.log", "log_format": "json"}
    )
    async with client_for(configured, database) as http:
        for _ in range(5):
            await http.get("/health")

    assert access_lines(tmp_path / "health.log") == []


async def test_a_failed_sign_in_is_logged_as_a_warning_without_the_password(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    configured = settings.model_copy(
        update={"log_to_file": True, "log_file": tmp_path / "auth.log", "log_format": "json"}
    )
    async with client_for(configured, database) as http:
        response = await http.post("/auth/login", json={"password": "swordfish"})
    assert response.status_code == 401

    written = (tmp_path / "auth.log").read_text()
    assert "Failed sign-in attempt" in written
    assert "swordfish" not in written


async def test_the_session_cookie_never_reaches_the_log(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    """The whole point of the redaction rules, asserted end to end."""
    configured = settings.model_copy(
        update={
            "log_to_file": True,
            "log_file": tmp_path / "secrets.log",
            "log_format": "json",
            "log_level": "DEBUG",  # the loudest it goes, to leave nowhere to hide
        }
    )
    async with client_for(configured, database) as http:
        login = await http.post("/auth/login", json={"password": OWNER_PASSWORD})
        assert login.status_code == 200
        await http.get("/me")

    cookie = login.cookies.get("cylist_session")
    assert cookie, "the test needs a real session cookie to look for"
    assert cookie not in (tmp_path / "secrets.log").read_text()


async def test_a_search_term_is_not_filed_away_with_the_request(
    settings: Settings, database: Database, tmp_path: Path
) -> None:
    """Query strings are user content, and are logged nowhere."""
    configured = settings.model_copy(
        update={
            "log_to_file": True,
            "log_file": tmp_path / "query.log",
            "log_format": "json",
            "log_level": "DEBUG",
        }
    )
    async with client_for(configured, database) as http:
        await http.get("/health", params={"q": "something-private"})

    access = access_lines(tmp_path / "query.log")
    assert [line["context"]["path"] for line in access] == ["/api/v1/health"], (
        "DEBUG should have logged the request at all"
    )
    assert "something-private" not in json.dumps(access)
