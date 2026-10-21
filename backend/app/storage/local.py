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
import re
import tempfile
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path, PurePosixPath
from typing import IO

from anyio import to_thread

from app.storage.base import BlobStore, BlobTooLargeError, StagedBlob, StoredBlob

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

    async def stage(self, chunks: AsyncIterator[bytes], *, max_bytes: int) -> StagedBlob:
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
            # Durable before it is staged, not when it is published: publishing
            # happens with the digest's lock held, and an fsync of a large file
            # is not something to do while other uploads wait on that lock.
            await to_thread.run_sync(self._seal, incoming)
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
        return StagedBlob(
            sha256=sha256, size=size, path=_relative_path(sha256), handle=incoming.name
        )

    async def publish(self, staged: StagedBlob) -> StoredBlob:
        deduplicated = await to_thread.run_sync(self._commit, Path(staged.handle), staged.path)
        # DEBUG, not INFO: the file row that results is already in the
        # activity trail, where it is queryable. What this adds is the
        # disk-side detail — the digest to find it under, and whether any
        # bytes were actually written — which is a question asked while
        # debugging storage, not while reading the log day to day.
        logger.debug(
            "Stored blob %s (%d bytes)%s",
            staged.sha256[:12],
            staged.size,
            " — already held" if deduplicated else "",
            extra={
                "context": {
                    "sha256": staged.sha256,
                    "bytes": staged.size,
                    "deduplicated": deduplicated,
                },
            },
        )
        return StoredBlob(
            sha256=staged.sha256, size=staged.size, path=staged.path, deduplicated=deduplicated
        )

    async def discard(self, staged: StagedBlob) -> None:
        await to_thread.run_sync(self._unlink, Path(staged.handle))

    def locate(self, path: str) -> Path:
        """The file under the root that ``path`` names.

        Every path this store hands out is a digest fanned out by
        :func:`_relative_path`, so one that is absolute or climbs with ``..``
        did not come from here. It is refused rather than joined: a row
        edited by hand, or restored from somewhere else, must not be able to
        turn a download into a read of any file the server can open.

        Raises:
            ValueError: if ``path`` would leave the root.
        """
        relative = PurePosixPath(path)
        if relative.is_absolute() or ".." in relative.parts:
            raise ValueError(f"Blob path {path!r} is outside the store.")
        return self._root / relative

    async def exists(self, path: str) -> bool:
        return await to_thread.run_sync(self.locate(path).is_file)

    async def remove(self, path: str) -> None:
        await to_thread.run_sync(self._unlink, self.locate(path))
        # INFO where storing is DEBUG, because this one is not reversible: if
        # a blob turns out to have been deleted while a row still referred to
        # it, this line is the only evidence of when that happened.
        logger.info("Removed blob", extra={"context": {"path": path}})

    async def held(self, *, idle_for: timedelta) -> dict[str, str]:
        return await to_thread.run_sync(self._held, time.time() - idle_for.total_seconds())

    async def clear_incoming(self, *, idle_for: timedelta) -> int:
        cleared = await to_thread.run_sync(
            self._clear_incoming, time.time() - idle_for.total_seconds()
        )
        if cleared:
            logger.info(
                "Cleared %d abandoned upload(s)", cleared, extra={"context": {"count": cleared}}
            )
        return cleared

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

    @staticmethod
    def _seal(incoming: IO[bytes]) -> None:
        """Make a finished temporary file durable, and let go of it."""
        incoming.flush()
        os.fsync(incoming.fileno())
        incoming.close()

    def _commit(self, source: Path, path: str) -> bool:
        """Publish a sealed temporary file at its digest path.

        Returns:
            True if the store already held these bytes, in which case the
            incoming copy is discarded rather than rewritten.
        """
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

    def _held(self, cutoff: float) -> dict[str, str]:
        """Walk the two fan-out levels for digests last modified before ``cutoff``.

        Only names that look like a digest in the bucket a digest would be in:
        anything else under the root was not put there by :meth:`publish`, and
        a sweep has no business deciding what it is.
        """
        found: dict[str, str] = {}
        if not self._root.is_dir():
            return found
        for first in self._root.iterdir():
            if not _is_bucket(first):
                continue
            for second in first.iterdir():
                if not _is_bucket(second):
                    continue
                for entry in second.iterdir():
                    name = entry.name
                    if (
                        _DIGEST.fullmatch(name)
                        and name[0:2] == first.name
                        and name[2:4] == second.name
                        and entry.is_file()
                        and entry.stat().st_mtime < cutoff
                    ):
                        found[name] = _relative_path(name)
        return found

    def _clear_incoming(self, cutoff: float) -> int:
        cleared = 0
        if not self._root.is_dir():
            return cleared
        for entry in self._root.glob(f"{TEMP_PREFIX}*"):
            try:
                if entry.stat().st_mtime >= cutoff:
                    continue
            except FileNotFoundError:
                continue  # its upload finished, or failed, while we looked
            self._unlink(entry)
            cleared += 1
        return cleared

    @staticmethod
    def _unlink(path: Path) -> None:
        path.unlink(missing_ok=True)


_DIGEST = re.compile(r"[0-9a-f]{64}")


def _is_bucket(entry: Path) -> bool:
    """A fan-out directory: two lowercase hex characters."""
    return (
        len(entry.name) == 2
        and all(c in "0123456789abcdef" for c in entry.name)
        and (entry.is_dir())
    )


def _relative_path(sha256: str) -> str:
    """Fan a digest out over two directory levels — ``ab/cd/abcd…``.

    A flat directory of blobs works until it holds a few hundred thousand
    entries, at which point listing or opening one gets slow on most
    filesystems. Two levels of 256 gives 65,536 buckets, which is plenty.
    """
    return f"{sha256[0:2]}/{sha256[2:4]}/{sha256}"
