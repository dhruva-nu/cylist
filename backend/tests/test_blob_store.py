"""The blob store: content addressing, atomic writes and the size cap.

These go straight at :class:`LocalBlobStore` rather than through the API. The
guarantees being checked — that a refused upload is not read to the end, that
nothing is left behind when one fails — are about what happens on the disk
during a write, which an HTTP response cannot show.
"""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from app.storage import BlobTooLargeError, LocalBlobStore
from app.storage.local import TEMP_PREFIX

CONTENT = b"the quarterly numbers, restated" * 40
DIGEST = hashlib.sha256(CONTENT).hexdigest()

NO_LIMIT = 1 << 30


async def stream(*chunks: bytes) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def leftovers(root: Path) -> list[Path]:
    """Temporary files still lying around in the store."""
    return sorted(root.glob(f"{TEMP_PREFIX}*"))


@pytest.fixture
def store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


class TestWriting:
    async def test_stores_the_content_under_its_digest(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert stored.sha256 == DIGEST
        assert stored.size == len(CONTENT)
        assert store.locate(stored.path).read_bytes() == CONTENT

    async def test_fans_the_digest_over_two_directory_levels(self, store: LocalBlobStore) -> None:
        """So a store with a million blobs is not a directory with a million entries."""
        stored = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert stored.path == f"{DIGEST[0:2]}/{DIGEST[2:4]}/{DIGEST}"

    async def test_reassembles_a_chunked_stream(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(b"one", b"two", b"three"), max_bytes=NO_LIMIT)

        assert store.locate(stored.path).read_bytes() == b"onetwothree"
        assert stored.sha256 == hashlib.sha256(b"onetwothree").hexdigest()

    async def test_leaves_no_temporary_file_behind(self, store: LocalBlobStore) -> None:
        """A committed write renames its temporary file rather than copying it."""
        await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert leftovers(store.locate("")) == []

    async def test_an_empty_stream_is_still_a_blob(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(), max_bytes=NO_LIMIT)

        assert stored.size == 0
        assert store.locate(stored.path).read_bytes() == b""


class TestDeduplication:
    async def test_identical_content_lands_in_one_place(self, store: LocalBlobStore) -> None:
        first = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)
        second = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert first.path == second.path
        assert second.deduplicated

    async def test_the_first_write_is_not_a_duplicate(self, store: LocalBlobStore) -> None:
        assert not (await store.write(stream(CONTENT), max_bytes=NO_LIMIT)).deduplicated

    async def test_a_duplicate_does_not_leave_its_copy_behind(self, store: LocalBlobStore) -> None:
        await store.write(stream(CONTENT), max_bytes=NO_LIMIT)
        await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert leftovers(store.locate("")) == []

    async def test_different_content_does_not_collide(self, store: LocalBlobStore) -> None:
        first = await store.write(stream(b"invoice A"), max_bytes=NO_LIMIT)
        second = await store.write(stream(b"invoice B"), max_bytes=NO_LIMIT)

        assert first.path != second.path


class TestTheSizeCap:
    async def test_refuses_a_stream_that_is_too_long(self, store: LocalBlobStore) -> None:
        with pytest.raises(BlobTooLargeError):
            await store.write(stream(b"x" * 101), max_bytes=100)

    async def test_accepts_a_stream_exactly_at_the_cap(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(b"x" * 100), max_bytes=100)

        assert stored.size == 100

    async def test_stops_reading_as_soon_as_the_cap_is_passed(self, store: LocalBlobStore) -> None:
        """The point of a cap is to refuse an upload, not to measure it afterwards."""
        consumed = 0

        async def counted() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(1000):
                consumed += 1
                yield b"x" * 100

        with pytest.raises(BlobTooLargeError):
            await store.write(counted(), max_bytes=250)

        assert consumed == 3

    async def test_writes_nothing_when_it_refuses(self, store: LocalBlobStore) -> None:
        with pytest.raises(BlobTooLargeError):
            await store.write(stream(b"x" * 500), max_bytes=100)

        root = store.locate("")
        assert leftovers(root) == []
        assert sorted(root.rglob("*")) == []

    async def test_a_stream_that_fails_leaves_nothing_behind(self, store: LocalBlobStore) -> None:
        """A dropped connection must not leave a fragment wearing a real digest."""

        async def broken() -> AsyncIterator[bytes]:
            yield b"the first half"
            raise ConnectionResetError("client went away")

        with pytest.raises(ConnectionResetError):
            await store.write(broken(), max_bytes=NO_LIMIT)

        assert sorted(store.locate("").rglob("*")) == []


class TestRemoving:
    async def test_deletes_the_content(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        await store.remove(stored.path)

        assert not await store.exists(stored.path)

    async def test_removing_what_is_not_there_is_not_an_error(self, store: LocalBlobStore) -> None:
        """Cleaning up after a partial failure must not fail in turn."""
        await store.remove("ab/cd/nothing-here")

    async def test_exists_reports_what_is_there(self, store: LocalBlobStore) -> None:
        stored = await store.write(stream(CONTENT), max_bytes=NO_LIMIT)

        assert await store.exists(stored.path)
        assert not await store.exists("ab/cd/nothing-here")
