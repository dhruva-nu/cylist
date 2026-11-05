"""Images in a card's description: uploaded to the blob store, served by id (CYLIST-182)."""

from __future__ import annotations

import hashlib
import zlib
from uuid import UUID

from httpx import AsyncClient, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import Blob, DescriptionImage, Task
from app.services.images import IMAGE_MAX_BYTES, sniffed_mime
from tests.conftest import INVITEE_PASSWORD, open_account

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
ADITI = {
    "name": "Aditi K",
    "kind": "team",
    "title": "Backend engineer",
    "responsibilities": "Payments and webhooks.",
}


def png_of(label: str) -> bytes:
    """A real one-pixel PNG, with ``label`` in a text chunk so each test's
    bytes are its own — the store is content-addressed and outlives a test."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return len(data).to_bytes(4, "big") + body + zlib.crc32(body).to_bytes(4, "big")

    header = (1).to_bytes(4, "big") * 2 + bytes([8, 0, 0, 0, 0])
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"tEXt", b"Comment\x00" + label.encode())
        + chunk(b"IDAT", zlib.compress(b"\x00\x00"))
        + chunk(b"IEND", b"")
    )


async def upload(
    client: AsyncClient, content: bytes, *, mime: str = "image/png", key: str = "ATL"
) -> Response:
    return await client.post(
        f"/projects/{key}/images", files={"file": ("screenshot.png", content, mime)}
    )


async def atlas(client: AsyncClient) -> None:
    response = await client.post("/projects", json=ATLAS)
    assert response.status_code == 201, response.text


class TestUpload:
    async def test_an_image_goes_to_the_blob_store_and_not_the_database(
        self, signed_in: AsyncClient, session: AsyncSession, settings: Settings
    ) -> None:
        await atlas(signed_in)
        content = png_of("stored")

        response = await upload(signed_in, content)

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["url"] == f"/api/v1/images/{body['id']}"
        assert body["mime"] == "image/png"
        assert body["size"] == len(content)
        digest = hashlib.sha256(content).hexdigest()
        assert (settings.blob_dir / digest[0:2] / digest[2:4] / digest).read_bytes() == content
        image = await session.get(DescriptionImage, UUID(body["id"]))
        assert image is not None
        assert image.blob.sha256 == digest

    async def test_the_type_is_read_off_the_bytes_not_the_label(
        self, signed_in: AsyncClient
    ) -> None:
        await atlas(signed_in)

        response = await upload(signed_in, png_of("mislabelled"), mime="text/html")

        assert response.status_code == 201, response.text
        assert response.json()["mime"] == "image/png"

    async def test_something_that_is_not_an_image_is_refused_unstored(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        await atlas(signed_in)

        for content in (b"<svg xmlns='http://www.w3.org/2000/svg'><script/></svg>", b"GIF"):
            response = await upload(signed_in, content, mime="image/svg+xml")
            assert response.status_code == 422, response.text

        assert await session.scalar(select(func.count()).select_from(DescriptionImage)) == 0
        assert await session.scalar(select(func.count()).select_from(Blob)) == 0

    async def test_an_image_over_the_cap_is_refused(self, signed_in: AsyncClient) -> None:
        await atlas(signed_in)
        content = png_of("huge") + b"\x00" * IMAGE_MAX_BYTES

        response = await upload(signed_in, content)

        assert response.status_code == 413, response.text

    async def test_the_same_screenshot_twice_is_one_blob(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        await atlas(signed_in)
        content = png_of("twice")

        first = (await upload(signed_in, content)).json()
        second = (await upload(signed_in, content)).json()

        assert first["id"] != second["id"]
        assert await session.scalar(select(func.count()).select_from(Blob)) == 1

    async def test_a_description_refers_to_it_and_keeps_only_the_reference(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        await atlas(signed_in)
        image = (await upload(signed_in, png_of("in a card"))).json()
        description = f"Broken on checkout:\n\n![screenshot]({image['url']})"

        created = await signed_in.post(
            "/projects/ATL/tasks",
            json={"title": "Fix checkout", "description": description, "type": "bug"},
        )

        assert created.status_code == 201, created.text
        stored = await session.scalar(select(Task.description))
        assert stored == description


class TestServing:
    async def test_a_member_gets_the_bytes_inline(self, signed_in: AsyncClient) -> None:
        await atlas(signed_in)
        content = png_of("served")
        image = (await upload(signed_in, content)).json()

        response = await signed_in.get(f"/images/{image['id']}")

        assert response.status_code == 200
        assert response.content == content
        assert response.headers["content-type"] == "image/png"
        assert "content-disposition" not in response.headers
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["cache-control"].startswith("private")

    async def test_somebody_not_on_the_project_is_told_nothing_is_there(
        self, signed_in: AsyncClient, other_client: AsyncClient
    ) -> None:
        await atlas(signed_in)
        image = (await upload(signed_in, png_of("fenced"))).json()
        _, token = await open_account(signed_in, ADITI, "aditi@cylist.dev")
        accepted = await other_client.post(
            "/auth/accept-invite", json={"token": token, "password": INVITEE_PASSWORD}
        )
        assert accepted.status_code == 200, accepted.text

        assert (await other_client.get(f"/images/{image['id']}")).status_code == 404
        assert (await upload(other_client, png_of("not theirs"))).status_code == 404

    async def test_an_unknown_image_is_404(self, signed_in: AsyncClient) -> None:
        response = await signed_in.get("/images/00000000-0000-7000-8000-000000000000")

        assert response.status_code == 404


class TestLifecycle:
    async def test_deleting_the_project_lets_the_bytes_go(
        self, signed_in: AsyncClient, session: AsyncSession
    ) -> None:
        await atlas(signed_in)
        await upload(signed_in, png_of("goes with the project"))

        deleted = await signed_in.delete("/projects/ATL", params={"confirm": "ATL"})

        assert deleted.status_code == 200, deleted.text
        assert await session.scalar(select(func.count()).select_from(Blob)) == 0


class TestSniffing:
    def test_each_allowed_type_is_known_by_its_signature(self) -> None:
        assert sniffed_mime(b"\x89PNG\r\n\x1a\n\x00\x00\x00\x0d") == "image/png"
        assert sniffed_mime(b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01") == "image/jpeg"
        assert sniffed_mime(b"GIF89a\x01\x00\x01\x00\x00\x00") == "image/gif"
        assert sniffed_mime(b"RIFF\x24\x00\x00\x00WEBP") == "image/webp"

    def test_anything_else_is_not(self) -> None:
        assert sniffed_mime(b"<?xml version=") is None
        assert sniffed_mime(b"RIFF\x24\x00\x00\x00WAVE") is None
        assert sniffed_mime(b"%PDF-1.7 hello") is None
