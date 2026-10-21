"""What a blob store has to be able to do.

An interface rather than a single class because the local disk is only the
first answer: the same operations describe an object store, and keeping
:mod:`app.services.files` behind them means moving to one later is a new
implementation rather than a rewrite.

Writing is two steps, :meth:`BlobStore.stage` and :meth:`BlobStore.publish`,
because the moment bytes appear under their digest has to be one the database
agrees to (CYLIST-129). Between the two, :mod:`app.services.blobs` claims the
row and the digest's lock; only then may an incoming copy be discarded as a
duplicate, since only then can nothing delete the copy already there.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path


@dataclass(frozen=True, slots=True)
class StoredBlob:
    """The result of writing a stream: what it turned out to be, and where."""

    sha256: str
    size: int
    path: str
    """Location within the store, to be handed back to :meth:`BlobStore.locate`
    and :meth:`BlobStore.remove`."""

    deduplicated: bool
    """True when the store already held these exact bytes and the incoming copy
    was discarded. The row the caller writes should reuse the existing blob."""


@dataclass(frozen=True, slots=True)
class StagedBlob:
    """Bytes the store has received in full but not yet put under their digest.

    Handed to :meth:`BlobStore.publish` or :meth:`BlobStore.discard`, and to
    nothing else: until it is published, nothing can read it, and a stage that
    is neither — its request died between the two — is left for
    :meth:`BlobStore.clear_incoming` to find.
    """

    sha256: str
    size: int
    path: str
    """Where the bytes will live once published."""

    handle: str
    """The store's own note of where it is holding the bytes meanwhile."""


class BlobTooLargeError(Exception):
    """A stream exceeded the cap it was written under.

    Deliberately not an :class:`app.core.errors.AppError`: the storage layer
    knows about bytes, not about HTTP status codes. The service that set the
    cap is what turns this into a 413.
    """

    def __init__(self, limit: int) -> None:
        super().__init__(f"Stream exceeded the {limit} byte limit.")
        self.limit = limit


class BlobStore(ABC):
    """Content-addressed storage for uploaded file contents."""

    @abstractmethod
    async def stage(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StagedBlob:
        """Consume a stream of bytes and hold them, durably, ready to publish.

        Implementations must stop reading as soon as ``max_bytes`` is passed,
        rather than measuring what they have already accepted: the point of the
        cap is to refuse a large upload without first finding room for it. A
        stream that fails or is refused leaves nothing staged.

        Raises:
            BlobTooLargeError: as soon as the stream passes ``max_bytes``.
        """

    @abstractmethod
    async def publish(self, staged: StagedBlob) -> StoredBlob:
        """Put staged bytes under their digest, or drop them if already there.

        Safe to call only while nothing can remove what is at the digest —
        which, for an upload, means while its transaction holds the digest's
        lock. See :func:`app.services.blobs.store_stream`.
        """

    @abstractmethod
    async def discard(self, staged: StagedBlob) -> None:
        """Throw staged bytes away. Already published or gone is not an error."""

    async def write(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StoredBlob:
        """Stage and publish in one go, with no database to agree with.

        For bytes no row will refer to before a sweep could see them — a test
        of the store itself. Anything that records a :class:`~app.models.file.Blob`
        goes through :func:`app.services.blobs.store_stream` instead, which
        claims the row between the two steps.
        """
        return await self.publish(await self.stage(chunks, max_bytes=max_bytes))

    @abstractmethod
    def locate(self, path: str) -> Path:
        """Return a filesystem path the bytes at ``path`` can be served from.

        A local-disk concession, and the reason the interface is an ABC rather
        than a protocol over streams: serving a download with ``sendfile`` is
        worth far more than the purity of hiding where the file is. A remote
        store would materialise a cached copy here.
        """

    @abstractmethod
    async def exists(self, path: str) -> bool:
        """Whether the bytes at ``path`` are actually there."""

    @abstractmethod
    async def remove(self, path: str) -> None:
        """Delete the bytes at ``path``. Missing is not an error.

        Only ever called by :func:`app.services.blobs.sweep`, holding the
        digest's lock and having seen no row for it.
        """

    @abstractmethod
    async def held(self, *, idle_for: timedelta) -> dict[str, str]:
        """Every published blob untouched for ``idle_for``, digest to path.

        What a sweep compares against the ``blob`` table. The age is a margin
        rather than the safety: the digest lock is what keeps a sweep off
        bytes an upload is about to claim.
        """

    @abstractmethod
    async def clear_incoming(self, *, idle_for: timedelta) -> int:
        """Remove staged bytes nobody has written to for ``idle_for``.

        What a request leaves behind when it dies between staging and
        publishing — a killed process, or a cancellation that interrupted the
        cleanup itself. Returns how many went.
        """
