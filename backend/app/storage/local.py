"""Blobs on the server's own disk.

Two properties matter here and neither is free:

*Atomicity.* A blob's name is a promise about its contents, so a file must
never appear at its digest path until every byte is on the disk. Uploads land
in a temporary file, are flushed and fsynced, and only then renamed into place
— ``rename`` within a filesystem is atomic, so a crash leaves either nothing or
a complete file, never a truncated one wearing a digest it does not match.

*Not blocking the event loop.* Every filesystem call runs in a worker thread.
A 200 MB upload is thousands of syscalls; on the loop thread they would stall
every other request for the duration.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path
from typing import IO

from anyio import to_thread

from app.storage.base import BlobStore, BlobTooLargeError, StoredBlob

logger = logging.getLogger(__name__)

TEMP_PREFIX = ".incoming-"
"""Dot-prefixed so a half-written upload is obviously not a blob if anyone
looks in the directory while one is in flight."""


class LocalBlobStore(BlobStore):
    """Content-addressed files under a root directory.

    Args:
        root: The directory blobs live in — ``DATA_DIR/blobs``.
    """

    def __init__(self, root: Path) -> None:
        self._root = root

    async def write(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StoredBlob:
        incoming = await to_thread.run_sync(self._begin)
        digest = hashlib.sha256()
        size = 0

        try:
            async for chunk in chunks:
                size += len(chunk)
                if size > max_bytes:
                    # Before writing the chunk, not after: the point is to
                    # refuse the upload rather than to find room for it first.
                    raise BlobTooLargeError(max_bytes)
                digest.update(chunk)
                await to_thread.run_sync(incoming.write, chunk)
        except BaseException as error:
            await to_thread.run_sync(self._abandon, incoming)
            # The one place that knows an upload was abandoned part-way. The
            # request will be answered with a 413 or a disconnect and nothing
            # else would say that bytes were written to the disk and then
            # taken off it again.
            logger.warning(
                "Upload abandoned after %d bytes: %s",
                size,
                type(error).__name__,
                extra={"context": {"bytes": size, "limit": max_bytes}},
            )
            raise

        sha256 = digest.hexdigest()
        path = _relative_path(sha256)
        deduplicated = await to_thread.run_sync(self._commit, incoming, path)
        # DEBUG, not INFO: the file row that results is already in the
        # activity trail, where it is queryable. What this adds is the
        # disk-side detail — the digest to find it under, and whether any
        # bytes were actually written — which is a question asked while
        # debugging storage, not while reading the log day to day.
        logger.debug(
            "Stored blob %s (%d bytes)%s",
            sha256[:12],
            size,
            " — already held" if deduplicated else "",
            extra={
                "context": {"sha256": sha256, "bytes": size, "deduplicated": deduplicated},
            },
        )
        return StoredBlob(sha256=sha256, size=size, path=path, deduplicated=deduplicated)

    def locate(self, path: str) -> Path:
        return self._root / path

    async def exists(self, path: str) -> bool:
        return await to_thread.run_sync(self.locate(path).is_file)

    async def remove(self, path: str) -> None:
        await to_thread.run_sync(self._unlink, self.locate(path))
        # INFO where storing is DEBUG, because this one is not reversible: if
        # a blob turns out to have been deleted while a row still referred to
        # it, this line is the only evidence of when that happened.
        logger.info("Removed blob", extra={"context": {"path": path}})

    # --- Everything below runs in a worker thread --------------------------

    def _begin(self) -> IO[bytes]:
        """Open a temporary file to stream into.

        It is created inside the store's own root because the commit is a
        rename, and a rename is only atomic — indeed, only possible — within
        one filesystem. The system temp directory is frequently another one.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        # Left open, and left on disk: the caller streams into it and then
        # either commits or abandons it, both of which close and clean up.
        return tempfile.NamedTemporaryFile(dir=self._root, prefix=TEMP_PREFIX, delete=False)

    def _commit(self, incoming: IO[bytes], path: str) -> bool:
        """Publish the finished temporary file at its digest path.

        Returns:
            True if the store already held these bytes, in which case the
            incoming copy is discarded rather than rewritten.
        """
        incoming.flush()
        os.fsync(incoming.fileno())
        incoming.close()

        source = Path(incoming.name)
        destination = self.locate(path)
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            self._unlink(source)
            return True

        source.replace(destination)
        return False

    def _abandon(self, incoming: IO[bytes]) -> None:
        """Throw away a stream that failed or was refused part-way through."""
        incoming.close()
        self._unlink(Path(incoming.name))

    @staticmethod
    def _unlink(path: Path) -> None:
        path.unlink(missing_ok=True)


def _relative_path(sha256: str) -> str:
    """Fan a digest out over two directory levels — ``ab/cd/abcd…``.

    A flat directory of blobs works until it holds a few hundred thousand
    entries, at which point listing or opening one gets slow on most
    filesystems. Two levels of 256 gives 65,536 buckets, which is plenty.
    """
    return f"{sha256[0:2]}/{sha256[2:4]}/{sha256}"
