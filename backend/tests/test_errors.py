"""What a request that Cylist did not expect to fail turns into.

A refusal Cylist meant — a 404, a 409 from a pre-check — has its own tests
beside the rule that raises it. These are about the rest: the exception
nobody caught, and the database refusing a write the services let through.
Each must still reach the client in the usual envelope, carry the request id
that finds it in the log, and be counted on the dashboard.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy.exc import DataError, IntegrityError

from app.config import Settings
from app.core.logging import REQUEST_ID_HEADER
from app.db import Database
from app.main import create_app
from tests.conftest import as_new, client_on


class _UnforeseenError(Exception):
    """Something no handler knows about."""


def _faulty_app(settings: Settings, database: Database) -> FastAPI:
    """The real app, plus routes that fail in each of the ways under test."""
    app = create_app(settings)

    @app.get("/api/v1/_test/boom")
    async def _boom() -> None:
        raise _UnforeseenError("the secret-ish detail that must stay in the log")

    @app.get("/api/v1/_test/integrity")
    async def _integrity() -> None:
        raise IntegrityError("INSERT ...", {}, Exception('Key (name)=("Search") exists'))

    @app.get("/api/v1/_test/data")
    async def _data() -> None:
        raise DataError("INSERT ...", {}, Exception("value too long"))

    @app.get("/api/v1/_test/disk")
    async def _disk() -> None:
        raise OSError(28, "No space left on device")

    return as_new(app, settings, database)


@pytest.fixture
async def faulty(settings: Settings, database: Database) -> AsyncIterator[AsyncClient]:
    async with client_on(_faulty_app(settings, database)) as http:
        yield http


class TestAnUnexpectedException:
    async def test_is_answered_in_the_envelope(self, faulty: AsyncClient) -> None:
        response = await faulty.get("/_test/boom")

        assert response.status_code == 500
        assert response.headers["content-type"] == "application/json"
        error = response.json()["error"]
        assert error["code"] == "internal_error"
        assert "secret-ish" not in response.text

    async def test_carries_a_request_id(self, faulty: AsyncClient) -> None:
        response = await faulty.get("/_test/boom", headers={REQUEST_ID_HEADER: "report-me"})

        assert response.headers[REQUEST_ID_HEADER] == "report-me"

    async def test_is_counted_as_a_500(self, faulty: AsyncClient) -> None:
        await faulty.get("/_test/boom")

        counts = (await faulty.get("/health/requests")).json()
        assert counts["status_5xx"] == 1
        assert counts["total"] == 1

    async def test_is_logged_once_with_its_traceback(
        self, settings: Settings, database: Database, tmp_path: Path
    ) -> None:
        log = tmp_path / "errors.log"
        configured = settings.model_copy(
            update={"log_to_file": True, "log_file": log, "log_format": "json"}
        )
        async with client_on(_faulty_app(configured, database)) as http:
            response = await http.get("/_test/boom")

        lines = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
        failures = [line for line in lines if line["logger"] == "app.request"]
        assert len(failures) == 1
        assert failures[0]["level"] == "ERROR"
        assert failures[0]["request_id"] == response.headers[REQUEST_ID_HEADER]
        assert "_UnforeseenError" in str(failures[0]["exception"])
        assert failures[0]["context"]["status"] == 500


class TestTheDatabaseRefusingAWrite:
    @pytest.mark.parametrize(
        ("path", "status", "code"),
        [
            ("/_test/integrity", 409, "conflict"),
            ("/_test/data", 422, "unprocessable"),
            ("/_test/disk", 503, "service_unavailable"),
        ],
    )
    async def test_gets_the_status_it_deserves(
        self, faulty: AsyncClient, path: str, status: int, code: str
    ) -> None:
        response = await faulty.get(path)

        assert response.status_code == status
        assert response.json()["error"]["code"] == code
        assert response.headers[REQUEST_ID_HEADER]

    async def test_does_not_echo_the_driver_message(self, faulty: AsyncClient) -> None:
        """Postgres quotes the clashing row's values; those are somebody's data."""
        response = await faulty.get("/_test/integrity")

        assert "Search" not in response.text


class TestAValidationFailure:
    async def test_names_the_field_and_not_the_value(self, signed_in: AsyncClient) -> None:
        response = await signed_in.post("/projects", json={"key": "ATL", "name": 42})

        assert response.status_code == 422
        fields = response.json()["error"]["details"]["fields"]
        assert fields
        for field in fields:
            assert set(field) <= {"type", "loc", "msg"}
