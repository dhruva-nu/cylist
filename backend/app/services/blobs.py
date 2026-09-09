"""Uploaded bytes, and who is still pointing at them.

Content is shared: two rows holding the same bytes share one
:class:`~app.models.file.Blob`, so a skill uploaded from a document already in
the project's folders costs one copy on disk. That is only safe while *every*
table that can refer to a blob is consulted before the bytes are deleted, and
"every table" is a fact that grows — it was file items alone, and skills are
the second. Hence one module: the list of referrers lives in :data:`REFERRERS`
and nothing else has to remember it.

Moved out of :mod:`app.services.files` when skills arrived. Deleting a file
whose content a skill also used would otherwise have tried to take the bytes
with it, and been stopped only by the ``ON DELETE RESTRICT`` on the skill's own
foreign key — a 500 in place of a delete that should simply have kept the blob.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Sequence
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import PayloadTooLargeError
from app.models.agent import Skill
from app.models.file import Blob, FileItem
from app.storage import BlobStore, BlobTooLargeError

CHUNK_BYTES = 1 << 20
"""How much of an upload is held in memory at once. Starlette has already
spooled anything past its own megabyte to a temporary file, so this bounds the
copy from there into the blob store rather than the receive itself."""

DEFAULT_MIME = "application/octet-stream"

REFERRERS: Sequence[InstrumentedAttribute[UUID | None]] = (
    FileItem.blob_id,
    Skill.blob_id,
)
"""Every column that can point at a blob.

Add to this when a new table refers to one, and :func:`collect_garbage` keeps
working. Leaving it out would not corrupt anything — the foreign keys are
``RESTRICT`` — but it would turn an unrelated delete into a 500.
"""


async def chunks(source: UploadFile, size: int = CHUNK_BYTES) -> AsyncIterator[bytes]:
    """Read an upload a chunk at a time.

    Deliberately not ``await source.read()``: that would pull however many
    hundred megabytes the client sent into memory, only to hand them to a store
    that is going to write them out again anyway.
    """
    while chunk := await source.read(size):
        yield chunk


async def store_upload(
    session: AsyncSession,
    store: BlobStore,
    source: UploadFile,
    *,
    max_bytes: int,
) -> tuple[Blob, int, str]:
    """Write an upload's bytes and return the blob holding them.

    Returns the blob, its size and the MIME type *this* upload declared —
    which is not necessarily the blob's own, because the content is what is
    shared and the label is not.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``. Written
            straight through in chunks, so an oversized file is refused
            part-way rather than after all of it has arrived.
    """
    try:
        stored = await store.write(chunks(source), max_bytes=max_bytes)
    except BlobTooLargeError as exc:
        raise PayloadTooLargeError(
            f"That file is larger than the {max_bytes // (1024 * 1024)} MB upload limit.",
            details={"max_bytes": max_bytes},
        ) from exc

    mime = source.content_type or DEFAULT_MIME
    blob = await session.scalar(select(Blob).where(Blob.sha256 == stored.sha256))
    if blob is None:
        blob = Blob(sha256=stored.sha256, size=stored.size, mime=mime, path=stored.path)
        session.add(blob)
        await session.flush()

    return blob, stored.size, mime


async def collect_garbage(session: AsyncSession, store: BlobStore, blob_ids: set[UUID]) -> None:
    """Delete the blobs among these that nothing points at any more.

    Rows go before bytes. The reverse order would, if the transaction were
    rolled back afterwards, leave a row promising content that is no longer
    there; this way the worst case is an unreferenced file on disk, which
    nothing can reach and which the next upload of the same content replaces.
    """
    if not blob_ids:
        return

    still_referenced: set[UUID] = set()
    for column in REFERRERS:
        held = await session.scalars(select(column).where(column.in_(blob_ids)))
        still_referenced.update(blob_id for blob_id in held if blob_id is not None)

    orphaned = list(
        await session.scalars(select(Blob).where(Blob.id.in_(blob_ids - still_referenced)))
    )
    if not orphaned:
        return

    await session.execute(delete(Blob).where(Blob.id.in_([blob.id for blob in orphaned])))
    await session.flush()
    for blob in orphaned:
        await store.remove(blob.path)
