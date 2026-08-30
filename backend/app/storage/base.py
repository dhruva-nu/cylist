"""What a blob store has to be able to do.

An interface rather than a single class because the local disk is only the
first answer: the same three operations describe an object store, and keeping
:mod:`app.services.files` behind them means moving to one later is a new
implementation rather than a rewrite.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from dataclasses import dataclass
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
    async def write(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StoredBlob:
        """Consume a stream of bytes and store them under their digest.

        Implementations must stop reading as soon as ``max_bytes`` is passed,
        rather than measuring what they have already accepted: the point of the
        cap is to refuse a large upload without first finding room for it.

        Raises:
            BlobTooLargeError: as soon as the stream passes ``max_bytes``.
        """

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
        """Delete the bytes at ``path``. Missing is not an error."""
