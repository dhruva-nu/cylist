"""Images in a card's description: storing them, and finding them again.

See :mod:`app.models.image` for why they belong to the project. The bytes go
through :func:`app.services.blobs.store_stream` like any upload; what is
particular here is that the content must *be* an image, because it is served
back inline into the page of whoever opens the card.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import multipart
from app.core.errors import NotFoundError, UnprocessableRequestError
from app.models.image import DescriptionImage
from app.models.project import Project
from app.services import blobs
from app.storage import BlobStore

IMAGE_MAX_BYTES = 10 * 1024 * 1024
"""A screenshot, generously. A description is read on every open of a card,
and a photograph straight off a camera is a file for the project's folders."""

SNIFF_BYTES = 12
"""Enough of the head of the content to tell every allowed type apart — WebP's
signature is the longest, at twelve."""


def sniffed_mime(head: bytes) -> str | None:
    """The image type these leading bytes announce, or None if not one we serve.

    Raster formats only. SVG is an image to a browser and a document with
    scripts in it to everything else, and is not worth being careful about
    for the sake of a screenshot.
    """
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image/webp"
    return None


def not_an_image() -> UnprocessableRequestError:
    return UnprocessableRequestError(
        "That file is not an image. Upload a PNG, JPEG, GIF or WebP.",
        details={"allowed": ["image/png", "image/jpeg", "image/gif", "image/webp"]},
    )


class _Sniffed:
    """A file part's chunks, checked to be an image before any reach the store.

    The check is made on the stream as it passes rather than after it is
    stored, so a file that is not an image is refused without being written.
    """

    def __init__(self, chunks: AsyncIterator[bytes]) -> None:
        self._chunks = chunks
        self.mime: str | None = None

    async def __aiter__(self) -> AsyncIterator[bytes]:
        head = bytearray()
        async for chunk in self._chunks:
            if self.mime is not None:
                yield chunk
                continue
            head += chunk
            if len(head) < SNIFF_BYTES:
                continue
            self.mime = sniffed_mime(bytes(head))
            if self.mime is None:
                raise not_an_image()
            yield bytes(head)
        if self.mime is None:
            # The whole content was shorter than any image's signature.
            raise not_an_image()


async def upload(
    session: AsyncSession,
    store: BlobStore,
    project: Project,
    form: multipart.Form,
    *,
    added_by: UUID | None,
) -> DescriptionImage:
    """Store the image a ``multipart/form-data`` body carries in its ``file`` part.

    Raises:
        PayloadTooLargeError: if the image is over :data:`IMAGE_MAX_BYTES`.
        RequestValidationError: if there is no file, or more than one.
        UnprocessableRequestError: if the content is not a PNG, JPEG, GIF or WebP.
    """
    stored: list[DescriptionImage] = []

    async def take(part: multipart.Part, _: object) -> None:
        if stored:
            raise multipart.invalid_field("file", "send one file")
        sniffed = _Sniffed(part.chunks())
        blob = await blobs.store_stream(session, store, aiter(sniffed), max_bytes=IMAGE_MAX_BYTES)
        if sniffed.mime is None:  # pragma: no cover - the stream raises first
            raise not_an_image()
        stored.append(
            DescriptionImage(project_id=project.id, blob=blob, mime=sniffed.mime, added_by=added_by)
        )

    await form.read(file_field="file", on_file=take, text_fields=frozenset())
    if not stored:
        raise multipart.invalid_field("file", "Field required", kind="missing")
    image = stored[0]
    session.add(image)
    await session.flush()
    return image


async def get(session: AsyncSession, image_id: UUID) -> DescriptionImage:
    """Fetch one image, 404-ing if nothing matches."""
    image = await session.get(DescriptionImage, image_id)
    if image is None:
        raise NotFoundError("No such image.", details={"image_id": str(image_id)})
    return image
