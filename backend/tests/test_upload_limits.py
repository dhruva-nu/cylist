"""Bodies are bounded before they are read, and uploads after they are allowed.

CYLIST-123. An upload's form used to be parsed — and its file spooled to the
container's ``/tmp`` — before the route's authentication ran, and nothing
capped a body but the blob store, while copying out of that spool. These
tests watch the request body itself: how much of it the server pulled off the
wire, and what was left on the disk.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from httpx import AsyncClient

from app.config import Settings
from app.core.body_limit import FORM_OVERHEAD_BYTES
from app.db import Database
from app.services.skill_folders import MAX_UNPACKED_BYTES
from tests.conftest import client_for, sign_in

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
MEGABYTE = 1024 * 1024


class WatchedBody:
    """A request body sent in chunks, with no ``Content-Length``, that counts
    how much of itself the server has read."""

    def __init__(self, body: bytes, chunk: int = 64 * 1024) -> None:
        self._body = body
        self._chunk = chunk
        self.read = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        for start in range(0, len(self._body), self._chunk):
            piece = self._body[start : start + self._chunk]
            self.read += len(piece)
            yield piece


BOUNDARY = "cylist-test-boundary"
CONTENT_TYPE = f"multipart/form-data; boundary={BOUNDARY}"


def form(*parts: tuple[str, str | None, bytes]) -> bytes:
    """A multipart body holding these parts, in this order — a file where a
    filename is given, a text field where it is None."""
    body = bytearray()
    for name, filename, content in parts:
        body += f"--{BOUNDARY}\r\n".encode()
        if filename is None:
            body += f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode()
        else:
            body += (
                f'Content-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                "Content-Type: application/octet-stream\r\n\r\n"
            ).encode()
        body += content + b"\r\n"
    body += f"--{BOUNDARY}--\r\n".encode()
    return bytes(body)


async def post_watched(
    client: AsyncClient, path: str, body: bytes, content_type: str = CONTENT_TYPE, **headers: str
) -> tuple[httpx.Response, WatchedBody]:
    watched = WatchedBody(body)
    response = await client.post(
        path, content=watched, headers={"content-type": content_type, **headers}
    )
    return response, watched


async def root_id(client: AsyncClient) -> str:
    response = await client.get("/projects/ATL/tree")
    assert response.status_code == 200, response.text
    folder_id: str = response.json()["id"]
    return folder_id


@pytest.fixture
async def capped(
    settings: Settings, database: Database, tmp_path: Path
) -> AsyncIterator[AsyncClient]:
    """Uploads capped at a megabyte, every other body at 64 KB, and a data
    directory of its own so what is on disk is this test's doing alone."""
    limited = settings.model_copy(
        update={"max_upload_mb": 1, "max_request_kb": 64, "data_dir": tmp_path}
    )
    async with client_for(limited, database) as http:
        await sign_in(http)
        await http.post("/projects", json=ATLAS)
        yield http


def on_disk(tmp_path: Path) -> list[Path]:
    return [path for path in (tmp_path / "blobs").rglob("*") if path.is_file()]


class TestAnUploadIsReadOnlyOnceItIsAllowed:
    async def test_nothing_is_read_without_credentials(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        folder = await root_id(capped)
        body = form(("file", "dump.bin", b"x" * 512 * 1024))
        capped.cookies.clear()

        response, watched = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 401
        assert watched.read == 0, "the body was read before the caller was known"
        assert on_disk(tmp_path) == []

    async def test_nothing_is_read_for_a_token_that_cannot_write(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        folder = await root_id(capped)
        token = (await capped.post("/tokens", json={"name": "r", "scopes": ["read"]})).json()
        body = form(("file", "dump.bin", b"x" * 512 * 1024))
        capped.cookies.clear()

        response, watched = await post_watched(
            capped,
            f"/folders/{folder}/upload",
            body,
            authorization=f"Bearer {token['token']}",
        )

        assert response.status_code == 403
        assert watched.read == 0
        assert on_disk(tmp_path) == []

    async def test_nothing_is_read_for_a_folder_that_does_not_exist(
        self, capped: AsyncClient
    ) -> None:
        body = form(("file", "dump.bin", b"x" * 512 * 1024))

        response, watched = await post_watched(
            capped, "/folders/00000000-0000-7000-8000-000000000000/upload", body
        )

        assert response.status_code == 404
        assert watched.read == 0

    async def test_a_skill_is_not_read_without_credentials(self, capped: AsyncClient) -> None:
        body = form(("file", "kit.md", b"x" * 512 * 1024))
        capped.cookies.clear()

        response, watched = await post_watched(capped, "/projects/ATL/skills", body)

        assert response.status_code == 401
        assert watched.read == 0

    async def test_an_allowed_upload_streams_through(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        folder = await root_id(capped)
        content = b"allowed and streamed\n" * 20_000
        body = form(("sensitivity", None, b"public"), ("file", "notes.txt", content))

        response, watched = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 201, response.text
        assert response.json()["size"] == len(content)
        assert response.json()["sensitivity"] == "public"
        assert watched.read == len(body)
        download = await capped.get(f"/items/{response.json()['id']}/download")
        assert download.content == content


class TestABodyOverItsLimit:
    async def test_is_refused_by_its_declared_length_before_a_byte_is_read(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        """Even signed out: the size is refused before anybody asks who is
        sending it."""
        folder = await root_id(capped)
        oversized = 1 * MEGABYTE + FORM_OVERHEAD_BYTES + 1
        capped.cookies.clear()

        response, watched = await post_watched(
            capped,
            f"/folders/{folder}/upload",
            b"x" * oversized,
            "multipart/form-data; boundary=x",
            **{"content-length": str(oversized)},
        )

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
        assert watched.read == 0
        assert on_disk(tmp_path) == []

    async def test_a_streamed_upload_is_stopped_part_way(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        """No length declared, so the count is kept as the body arrives — and
        the request is answered the moment it passes the cap, unread past it."""
        folder = await root_id(capped)
        content = b"x" * (4 * MEGABYTE)
        body = form(("file", "dump.bin", content))

        response, watched = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
        assert watched.read < 3 * MEGABYTE, "the body was read to its end"
        assert on_disk(tmp_path) == []
        assert (await capped.get(f"/folders/{folder}/children")).json()["items"] == []

    async def test_a_file_over_the_upload_limit_inside_the_overhead_is_refused_too(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        """The overhead is for the form around the file, not for the file: the
        store holds the file itself to the configured size."""
        folder = await root_id(capped)
        body = form(("file", "dump.bin", b"x" * (MEGABYTE + 1)))

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 413
        assert on_disk(tmp_path) == []

    async def test_a_json_body_is_refused_by_its_declared_length(self, capped: AsyncClient) -> None:
        response = await capped.post(
            "/projects", json={"key": "BIG", "name": "Big", "description": "x" * 100_000}
        )

        assert response.status_code == 413
        assert response.json()["error"]["details"] == {"max_bytes": 64 * 1024}
        assert [p["key"] for p in (await capped.get("/projects")).json()] == ["ATL"]

    async def test_a_streamed_json_body_is_a_413_not_a_400(self, capped: AsyncClient) -> None:
        """FastAPI reports a failure while reading a JSON body as a 400. The
        refusal is the limit's, and the client is told so."""
        body = b'{"key": "BIG", "name": "' + b"x" * 1_000_000 + b'"}'

        response, watched = await post_watched(capped, "/projects", body, "application/json")

        assert response.status_code == 413
        assert response.json()["error"]["code"] == "payload_too_large"
        assert watched.read < len(body)

    async def test_an_upload_route_takes_more_than_a_json_one(self, capped: AsyncClient) -> None:
        """The JSON limit is 64 KB here; an upload of several times that is
        within the upload limit, and goes through."""
        folder = await root_id(capped)
        body = form(("file", "big.bin", b"y" * 300_000))

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 201, response.text

    async def test_is_counted_and_logged_like_any_other_response(self, capped: AsyncClient) -> None:
        await capped.post("/projects", json={"key": "BIG", "name": "x" * 100_000})

        counts = (await capped.get("/health/requests")).json()
        assert counts["status_4xx"] >= 1


class TestAStreamedSkill:
    async def test_a_single_file_past_a_folders_cap_is_streamed_to_the_store(
        self, settings: Settings, database: Database, tmp_path: Path
    ) -> None:
        """Too big to be part of a folder, so it is not held in memory to find
        out: it goes on to the store and is a single-file skill."""
        roomy = settings.model_copy(update={"max_upload_mb": 64, "data_dir": tmp_path})
        content = b"z" * (MAX_UNPACKED_BYTES + MEGABYTE)
        async with client_for(roomy, database) as http:
            await sign_in(http)
            await http.post("/projects", json=ATLAS)
            body = form(("file", "kit.zip", content))

            response, _ = await post_watched(http, "/projects/ATL/skills", body)

            assert response.status_code == 201, response.text
            assert response.json()["name"] == "kit.zip"
            assert response.json()["size"] == len(content)

    async def test_a_folder_named_after_such_a_file_is_refused(
        self, settings: Settings, database: Database, tmp_path: Path
    ) -> None:
        roomy = settings.model_copy(update={"max_upload_mb": 64, "data_dir": tmp_path})
        async with client_for(roomy, database) as http:
            await sign_in(http)
            await http.post("/projects", json=ATLAS)
            body = form(
                ("file", "kit/big.bin", b"z" * (MAX_UNPACKED_BYTES + 1)), ("folder", None, b"kit")
            )

            response, _ = await post_watched(http, "/projects/ATL/skills", body)

            assert response.status_code == 422
            assert (await http.get("/projects/ATL/skills")).json() == []

    async def test_fields_after_the_file_still_count(self, capped: AsyncClient) -> None:
        """A browser sends the file first. The description after it is read."""
        body = form(
            ("file", "tidy.md", b"Tidy the board.\n"), ("description", None, b"Tidies the board")
        )

        response, _ = await post_watched(capped, "/projects/ATL/skills", body)

        assert response.status_code == 201, response.text
        assert response.json()["description"] == "Tidies the board"

    async def test_a_folder_named_after_its_files_is_still_a_folder(
        self, capped: AsyncClient
    ) -> None:
        body = form(
            ("file", "kit/SKILL.md", b"Do the thing.\n"),
            ("file", "kit/run.sh", b"echo hi\n"),
            ("executable", None, b"kit/run.sh"),
            ("folder", None, b"kit"),
        )

        response, _ = await post_watched(capped, "/projects/ATL/skills", body)

        assert response.status_code == 201, response.text
        assert response.json()["name"] == "kit.zip"
        folder = (await capped.get(f"/skills/{response.json()['id']}/folder")).json()
        assert {f["path"]: f["executable"] for f in folder["files"]} == {
            "SKILL.md": False,
            "run.sh": True,
        }

    async def test_a_description_past_its_length_is_refused(self, capped: AsyncClient) -> None:
        body = form(("description", None, b"x" * 2001), ("file", "tidy.md", b"Tidy.\n"))

        response, _ = await post_watched(capped, "/projects/ATL/skills", body)

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"


class TestTheFormItself:
    async def test_a_field_sent_first_is_checked_before_the_file_is_stored(
        self, capped: AsyncClient, tmp_path: Path
    ) -> None:
        folder = await root_id(capped)
        body = form(
            ("added_by", None, b"00000000-0000-7000-8000-000000000000"),
            ("file", "brief.pdf", b"%PDF never stored"),
        )

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 422
        assert on_disk(tmp_path) == []

    async def test_a_field_that_does_not_parse_is_a_validation_failure(
        self, capped: AsyncClient
    ) -> None:
        folder = await root_id(capped)
        body = form(("file", "brief.pdf", b"%PDF"), ("sensitivity", None, b"top-secret"))

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 422
        error = response.json()["error"]
        assert error["code"] == "validation_failed"
        assert error["details"]["fields"][0]["loc"] == ["body", "sensitivity"]

    async def test_no_file_is_a_validation_failure(self, capped: AsyncClient) -> None:
        folder = await root_id(capped)

        response, _ = await post_watched(
            capped, f"/folders/{folder}/upload", form(("sensitivity", None, b"public"))
        )

        assert response.status_code == 422
        assert response.json()["error"]["details"]["fields"][0]["loc"] == ["body", "file"]

    async def test_two_files_are_refused_before_the_second_is_stored(
        self, capped: AsyncClient
    ) -> None:
        folder = await root_id(capped)
        body = form(("file", "one.txt", b"one"), ("file", "two.txt", b"two"))

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 422
        assert (await capped.get(f"/folders/{folder}/children")).json()["items"] == []

    async def test_a_body_that_is_not_multipart_is_refused(self, capped: AsyncClient) -> None:
        folder = await root_id(capped)

        response = await capped.post(f"/folders/{folder}/upload", json={"file": "x"})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_failed"

    async def test_a_body_cut_off_part_way_is_refused(self, capped: AsyncClient) -> None:
        folder = await root_id(capped)
        body = form(("file", "cut.txt", b"the whole thing"))[:-30]

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 422
        assert (await capped.get(f"/folders/{folder}/children")).json()["items"] == []

    async def test_unknown_fields_are_ignored(self, capped: AsyncClient) -> None:
        folder = await root_id(capped)
        body = form(("colour", None, b"teal"), ("file", "notes.txt", b"notes"))

        response, _ = await post_watched(capped, f"/folders/{folder}/upload", body)

        assert response.status_code == 201, response.text
