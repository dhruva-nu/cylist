"""When uploaded bytes may leave the disk, and who decides (CYLIST-129).

A delete takes rows; only the sweep takes bytes, and only for a digest with no
row whose lock it can get. These tests put two transactions against each other
for real — one held open while the other runs — because the bugs they guard
against lived in exactly that interleaving: a duplicate upload discarding its
copy while a delete was removing the one it meant to share, and a delete
unlinking bytes before a rollback brought their row back.

Each test gets a store of its own under ``tmp_path``. The application's store
is shared by every test of a worker and outlives the rows the suite truncates,
so counting what a sweep removed from it would count other tests' leftovers.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import pytest
from fastapi import FastAPI
from httpx import AsyncClient
from sqlalchemy import func, select

from app import cli, main
from app.config import Settings
from app.db import Database
from app.models import Blob, FileItem, ItemKind, ItemSource
from app.services import blobs, files
from app.storage import LocalBlobStore
from app.storage.local import TEMP_PREFIX

ATLAS = {"key": "ATL", "name": "Atlas Billing Migration"}
NO_LIMIT = 1 << 30
NOW = timedelta(0)
"""An idle margin of nothing: sweep whatever is unreferenced, however fresh."""

BLOCKED_FOR = 0.3
"""How long a transaction is watched to show it is waiting on a lock. Long
enough that one which was not waiting would have finished."""


def content_of(label: str) -> bytes:
    return f"cylist lifecycle fixture: {label}\n".encode() * 16


async def stream(content: bytes) -> AsyncIterator[bytes]:
    yield content


def on_disk(store: LocalBlobStore, content: bytes) -> Path:
    digest = hashlib.sha256(content).hexdigest()
    return store.locate(f"{digest[0:2]}/{digest[2:4]}/{digest}")


def held_at(handle: str) -> bytes:
    """What a staged blob holds, read from where the local store keeps it."""
    return Path(handle).read_bytes()


SWEEP_IDLE_FOR = timedelta(seconds=Settings.model_fields["blob_sweep_idle_seconds"].default)
"""What a deployment that sets nothing waits before a sweep looks at a file."""


def age(path: Path, by: timedelta) -> None:
    """Make a file look as if nothing has touched it for ``by``."""
    then = time.time() - by.total_seconds()
    os.utime(path, (then, then))


@pytest.fixture
def store(tmp_path: Path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


@pytest.fixture
async def folder(signed_in: AsyncClient) -> UUID:
    """The root folder of a fresh project, for items made straight in the database."""
    await signed_in.post("/projects", json=ATLAS)
    root = (await signed_in.get("/projects/ATL/folders")).json()["items"][0]["id"]
    return UUID(root)


async def file_into(
    database: Database, store: LocalBlobStore, folder: UUID, content: bytes, name: str
) -> UUID:
    """Upload ``content`` as a file in ``folder``, committed, the way the API does."""
    async with database.session() as session:
        blob = await blobs.store_stream(session, store, stream(content), max_bytes=NO_LIMIT)
        item = _item(folder, blob.id, name)
        session.add(item)
        await session.flush()
        return item.id


def _item(folder: UUID, blob_id: UUID, name: str) -> FileItem:
    return FileItem(
        folder_id=folder,
        kind=ItemKind.FILE,
        name=name,
        blob_id=blob_id,
        source=ItemSource.UPLOAD,
        mime="application/pdf",
    )


async def blob_count(database: Database) -> int:
    async with database.session() as session:
        return await session.scalar(select(func.count()).select_from(Blob)) or 0


async def delete_in_own_transaction(database: Database, item_id: UUID) -> None:
    async with database.session() as session:
        item = await session.get(FileItem, item_id)
        assert item is not None
        await files.delete_item(session, item)


class TestStaging:
    async def test_staged_bytes_are_not_yet_under_their_digest(self, store: LocalBlobStore) -> None:
        content = content_of("staged")

        staged = await store.stage(stream(content), max_bytes=NO_LIMIT)

        assert not on_disk(store, content).exists()
        assert held_at(staged.handle) == content

    async def test_publishing_puts_them_there(self, store: LocalBlobStore) -> None:
        content = content_of("published")
        staged = await store.stage(stream(content), max_bytes=NO_LIMIT)

        stored = await store.publish(staged)

        assert not stored.deduplicated
        assert on_disk(store, content).read_bytes() == content
        assert sorted(store.locate("").glob(f"{TEMP_PREFIX}*")) == []

    async def test_publishing_a_duplicate_keeps_the_copy_already_there(
        self, store: LocalBlobStore
    ) -> None:
        content = content_of("twice")
        await store.write(stream(content), max_bytes=NO_LIMIT)

        stored = await store.publish(await store.stage(stream(content), max_bytes=NO_LIMIT))

        assert stored.deduplicated
        assert sorted(store.locate("").glob(f"{TEMP_PREFIX}*")) == []

    async def test_discarding_leaves_nothing(self, store: LocalBlobStore) -> None:
        staged = await store.stage(stream(content_of("dropped")), max_bytes=NO_LIMIT)

        await store.discard(staged)

        assert [p for p in store.locate("").rglob("*") if p.is_file()] == []


class TestADeleteTakesRowsOnly:
    async def test_the_bytes_stay_until_the_sweep(
        self, database: Database, store: LocalBlobStore, folder: UUID
    ) -> None:
        content = content_of("deleted")
        item = await file_into(database, store, folder, content, "brief.pdf")

        await delete_in_own_transaction(database, item)

        assert await blob_count(database) == 0
        assert on_disk(store, content).exists(), "a request never unlinks"

        swept = await blobs.sweep(database, store, idle_for=NOW)

        assert swept.orphans == 1
        assert not on_disk(store, content).exists()

    async def test_a_rolled_back_delete_leaves_its_row_with_its_bytes(
        self, database: Database, store: LocalBlobStore, folder: UUID
    ) -> None:
        """The bug: the bytes were unlinked before commit, the rows came back."""
        content = content_of("rolled back")
        item = await file_into(database, store, folder, content, "brief.pdf")

        with pytest.raises(RuntimeError, match="later in the request"):
            async with database.session() as session:
                doomed = await session.get(FileItem, item)
                assert doomed is not None
                await files.delete_item(session, doomed)
                raise RuntimeError("something later in the request failed")

        assert await blob_count(database) == 1
        assert on_disk(store, content).read_bytes() == content
        assert (await blobs.sweep(database, store, idle_for=NOW)).orphans == 0


class TestADuplicateUploadRacingADelete:
    async def test_an_upload_waiting_on_the_delete_puts_its_bytes_back(
        self, database: Database, store: LocalBlobStore, folder: UUID
    ) -> None:
        """The delete goes first and commits; the upload of the same bytes
        claims a fresh row behind it, and that row has its content."""
        content = content_of("raced, delete first")
        item = await file_into(database, store, folder, content, "brief.pdf")

        async with database.session() as deleter:
            doomed = await deleter.get(FileItem, item)
            assert doomed is not None
            await files.delete_item(deleter, doomed)
            upload = asyncio.create_task(
                file_into(database, store, folder, content, "brief-again.pdf")
            )
            await asyncio.sleep(BLOCKED_FOR)
            assert not upload.done(), "the upload waits on the delete's row"

        again = await asyncio.wait_for(upload, 10)
        await blobs.sweep(database, store, idle_for=NOW)

        async with database.session() as session:
            kept = await session.get(FileItem, again)
            assert kept is not None and kept.blob is not None
            assert store.locate(kept.blob.path).read_bytes() == content

    async def test_a_delete_waiting_on_the_upload_sees_its_new_reference(
        self, database: Database, store: LocalBlobStore, folder: UUID
    ) -> None:
        """The upload claims the row first; the delete waits for it, then
        counts the file it added and keeps the blob — no foreign-key 500."""
        content = content_of("raced, upload first")
        item = await file_into(database, store, folder, content, "brief.pdf")

        async with database.session() as uploader:
            blob = await blobs.store_stream(uploader, store, stream(content), max_bytes=NO_LIMIT)
            delete = asyncio.create_task(delete_in_own_transaction(database, item))
            await asyncio.sleep(BLOCKED_FOR)
            assert not delete.done(), "the delete waits on the upload's claim"
            uploader.add(_item(folder, blob.id, "brief-again.pdf"))
            await uploader.flush()

        await asyncio.wait_for(delete, 10)
        await blobs.sweep(database, store, idle_for=NOW)

        assert await blob_count(database) == 1
        assert on_disk(store, content).read_bytes() == content


class TestTheSweep:
    async def test_skips_a_digest_an_upload_is_claiming(
        self, database: Database, store: LocalBlobStore, folder: UUID
    ) -> None:
        """Its row is not committed yet, so the sweep sees none — and must
        still leave the bytes, because the upload holds the digest's lock."""
        content = content_of("claimed mid-sweep")
        item = await file_into(database, store, folder, content, "brief.pdf")
        await delete_in_own_transaction(database, item)

        async with database.session() as uploader:
            blob = await blobs.store_stream(uploader, store, stream(content), max_bytes=NO_LIMIT)
            swept = await blobs.sweep(database, store, idle_for=NOW)
            assert swept.orphans == 0
            uploader.add(_item(folder, blob.id, "brief-again.pdf"))
            await uploader.flush()

        assert (await blobs.sweep(database, store, idle_for=NOW)).orphans == 0
        assert on_disk(store, content).read_bytes() == content

    async def test_takes_what_a_rolled_back_upload_left(
        self, database: Database, store: LocalBlobStore
    ) -> None:
        content = content_of("upload rolled back")
        with pytest.raises(RuntimeError, match="after the bytes"):
            async with database.session() as session:
                await blobs.store_stream(session, store, stream(content), max_bytes=NO_LIMIT)
                raise RuntimeError("the request failed after the bytes were stored")
        assert on_disk(store, content).exists()

        assert (await blobs.sweep(database, store, idle_for=NOW)).orphans == 1
        assert not on_disk(store, content).exists()

    async def test_leaves_fresh_orphans_to_the_margin(
        self, database: Database, store: LocalBlobStore
    ) -> None:
        content = content_of("fresh orphan")
        await store.write(stream(content), max_bytes=NO_LIMIT)

        assert (await blobs.sweep(database, store, idle_for=SWEEP_IDLE_FOR)).orphans == 0
        assert on_disk(store, content).exists()

        age(on_disk(store, content), SWEEP_IDLE_FOR * 2)
        assert (await blobs.sweep(database, store, idle_for=SWEEP_IDLE_FOR)).orphans == 1

    async def test_clears_abandoned_uploads_but_not_ones_arriving(
        self, database: Database, store: LocalBlobStore
    ) -> None:
        root = store.locate("")
        root.mkdir(parents=True)
        abandoned = root / f"{TEMP_PREFIX}abandoned"
        arriving = root / f"{TEMP_PREFIX}arriving"
        abandoned.write_bytes(b"half a file")
        arriving.write_bytes(b"still coming")
        age(abandoned, SWEEP_IDLE_FOR * 2)

        swept = await blobs.sweep(database, store, idle_for=SWEEP_IDLE_FOR)

        assert swept.incoming == 1
        assert not abandoned.exists()
        assert arriving.exists()

    async def test_ignores_what_it_did_not_put_there(
        self, database: Database, store: LocalBlobStore
    ) -> None:
        """Only a digest in its own bucket is a blob; anything else is not the
        sweep's to judge."""
        root = store.locate("")
        stray = [root / "ab" / "cd" / "notes.txt", root / "ab" / "cd" / ("0" * 64), root / "x"]
        for path in stray:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"not a blob")
            age(path, SWEEP_IDLE_FOR * 2)

        swept = await blobs.sweep(database, store, idle_for=NOW)

        assert swept == blobs.Swept(orphans=0, incoming=0)
        assert all(path.exists() for path in stray)

    async def test_one_process_sweeps_a_pass(
        self, database: Database, store: LocalBlobStore
    ) -> None:
        async with database.session() as other:
            await other.execute(select(func.pg_advisory_xact_lock(blobs.SWEEPER_LEASE)))
            assert await blobs.sweep_if_first(database, store, idle_for=SWEEP_IDLE_FOR) is None

        assert await blobs.sweep_if_first(database, store, idle_for=SWEEP_IDLE_FOR) == blobs.Swept(
            orphans=0, incoming=0
        )


class TestThePeriodicSweep:
    async def test_the_application_sweeps_on_its_own(
        self, app: FastAPI, settings: Settings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store = LocalBlobStore(settings.blob_dir)
        content = content_of("swept by the loop")
        await store.write(stream(content), max_bytes=NO_LIMIT)
        age(on_disk(store, content), SWEEP_IDLE_FOR * 2)
        monkeypatch.setattr(
            app.state,
            "settings",
            settings.model_copy(update={"blob_sweep_every_seconds": 0.05}),
        )

        loop = asyncio.create_task(main._sweep_blobs_forever(app))
        try:
            for _ in range(100):
                if not on_disk(store, content).exists():
                    break
                await asyncio.sleep(0.05)
        finally:
            loop.cancel()
            with pytest.raises(asyncio.CancelledError):
                await loop

        assert not on_disk(store, content).exists()


class TestTheCommand:
    def test_blobs_gc_sweeps_now(
        self,
        settings: Settings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        """``python -m app.cli blobs gc``, against a data directory of its own."""
        configured = settings.model_copy(update={"data_dir": tmp_path})
        monkeypatch.setattr(cli, "get_settings", lambda: configured)
        store = LocalBlobStore(configured.blob_dir)
        content = content_of("collected by hand")
        asyncio.run(store.write(stream(content), max_bytes=NO_LIMIT))
        stale = store.locate("") / f"{TEMP_PREFIX}stale"
        stale.write_bytes(b"left behind")

        assert cli.main(["blobs", "gc", "--idle-minutes", "0"]) == 0

        assert not on_disk(store, content).exists()
        assert not stale.exists()
        printed = capsys.readouterr().out
        assert "Removed 1 unreferenced blob(s) and 1 abandoned upload(s)." in printed

    def test_refuses_a_negative_margin(self) -> None:
        assert cli.main(["blobs", "gc", "--idle-minutes", "-5"]) == 2
