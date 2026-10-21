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

Rows and bytes leave at different times (CYLIST-129). A request deletes rows
and never touches the disk; :func:`sweep` alone removes bytes, for a digest
with no row, holding that digest's advisory lock. An upload holds the same
lock, shared, from claiming its row until it commits — so between them, bytes
are never removed while a row for them exists or is about to.
"""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

from sqlalchemy import delete, func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import InstrumentedAttribute

from app.core.errors import PayloadTooLargeError
from app.db import Database
from app.models.agent import Skill
from app.models.file import Blob, FileItem, Folder
from app.storage import BlobStore, BlobTooLargeError, StagedBlob

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


logger = logging.getLogger(__name__)

CLAIM_ATTEMPTS = 3
"""How often :func:`_claim` retries a row that a delete took from under it.
Once is the race; a third time would mean deletes of the same bytes arriving
faster than this upload can insert, which is not a race but a bug."""

SWEEP_IDLE_FOR = timedelta(hours=1)
"""How long bytes on disk must have gone untouched before a sweep looks at
them. A margin, not the safety — the digest lock is that — but it keeps a
sweep away from an upload still arriving into its temporary file, which holds
no lock until it has finished."""

SWEEP_EVERY = timedelta(hours=1)
"""How often the application sweeps. Deleted content stays on disk at most
about this plus :data:`SWEEP_IDLE_FOR`; ``python -m app.cli blobs gc`` is the
way to reclaim it sooner."""

BLOB_LOCK_CLASS = 0x6379_6C69
"""The first key of every digest lock: ``cyli``. Advisory locks taken with two
32-bit keys never collide with those taken with one 64-bit key, like
:data:`SWEEPER_LEASE`."""

SWEEPER_LEASE = 0x6379_6C69_7374_0002
"""The advisory lock a periodic sweep holds — the reaper's lease, next serial.
See :func:`sweep_if_first`."""


def digest_lock(sha256: str) -> tuple[int, int]:
    """The advisory lock keys for one digest.

    Uploads hold it shared, from claiming the row to committing; a sweep holds
    it exclusively while it decides whether to remove the bytes. Thirty-two
    bits of the digest: two digests sharing them only make one wait for the
    other, which is a cost, not an error.
    """
    return BLOB_LOCK_CLASS, int.from_bytes(bytes.fromhex(sha256[:8]), "big", signed=True)


MIME_MAX_LENGTH = 255
"""The ``mime`` columns' width, on files and on skills alike."""

_MIME_TYPE = re.compile(r"[a-z0-9][a-z0-9!#$&^_.+-]*/[a-z0-9][a-z0-9!#$&^_.+-]*")
"""A media type's essence — ``type/subtype`` in RFC 6838's characters."""


def declared_mime(declared: str | None) -> str:
    """The type an upload declared, normalised, or the generic one if it
    declared nothing usable (CYLIST-136).

    Whatever the client sent in the part's ``Content-Type``, which is not
    checked against the bytes and is only ever a label — but it is a label a
    download sends back, so it is reduced to its lower-cased ``type/subtype``
    first. Parameters go, and with them anything a client could smuggle into
    a header after a ``;``; something that is not a media type at all, or is
    longer than the column, is no type, and stored as the generic one rather
    than refusing a file whose content is fine.

    Run again on the way out, so a row stored before this rule came in is
    served under the type it would have been stored as now.
    """
    if not declared:
        return DEFAULT_MIME
    essence = declared.split(";", 1)[0].strip().lower()
    if len(essence) > MIME_MAX_LENGTH or not _MIME_TYPE.fullmatch(essence):
        return DEFAULT_MIME
    return essence


async def store_bytes(
    session: AsyncSession, store: BlobStore, data: bytes, *, max_bytes: int
) -> Blob:
    """Write bytes the server itself assembled and return the blob holding them.

    Separate from :func:`store_stream` because not everything stored came off
    the wire as one part: a skill uploaded as a folder is zipped here and
    stored as what was assembled, which has no part behind it and no content
    type of its own to report.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
    """

    async def once() -> AsyncIterator[bytes]:
        yield data

    return await store_stream(session, store, once(), max_bytes=max_bytes)


async def store_stream(
    session: AsyncSession, store: BlobStore, stream: AsyncIterator[bytes], *, max_bytes: int
) -> Blob:
    """Write a stream under its digest, reusing the row if the bytes are held.

    The entry point for anything that stores bytes and then refers to them;
    :func:`store_bytes` is this with the stream made for it. An upload's
    stream is its file part read straight off the socket (see
    :mod:`app.core.multipart`, CYLIST-123), so nothing lands on the disk but
    the store's own staging file — no spool beforehand, and nothing at all for
    a request its route refused before reading.

    In three steps, and the order is the fix for CYLIST-129:

    1. Stage the bytes — all of them, fsynced — with no lock held, since a
       large upload can take minutes to arrive.
    2. Take the digest's lock for the rest of the transaction, and claim the
       row with ``FOR SHARE``. A delete in another transaction now waits for
       this one before it can decide the blob is unreferenced; a sweep cannot
       take the digest's lock to remove the bytes.
    3. Only then publish: rename into place, or — if the digest's file is
       already there — discard the incoming copy, knowing the copy that stays
       cannot be removed before this transaction's row is visible.

    Discarding a duplicate *before* step 2 is what used to lose data: a delete
    could take the row and the bytes between the check and the select, and the
    row this upload then wrote pointed at nothing.

    Raises:
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
    """
    try:
        staged = await store.stage(stream, max_bytes=max_bytes)
    except BlobTooLargeError as exc:
        raise PayloadTooLargeError(
            f"That file is larger than the {max_bytes // (1024 * 1024)} MB upload limit.",
            details={"max_bytes": max_bytes},
        ) from exc

    try:
        await session.execute(
            select(func.pg_advisory_xact_lock_shared(*digest_lock(staged.sha256)))
        )
        blob = await _claim(session, staged)
        await store.publish(staged)
    except BaseException:
        # Not published, so the staged copy is nobody's; if this cleanup is
        # itself interrupted, the sweep's pass over abandoned uploads finds it.
        await store.discard(staged)
        raise
    return blob


async def _claim(session: AsyncSession, staged: StagedBlob) -> Blob:
    """The digest's row, inserted if absent and held ``FOR SHARE`` either way.

    Insert-if-absent in one statement rather than a SELECT then an INSERT: two
    people uploading the same file for the first time both found no row, and
    the second INSERT failed on the digest's unique index as a 500.

    A loop because the row can vanish between the two statements: a delete
    that had locked it commits, the insert had already found it present, and
    the select — waiting on that delete's lock — comes back empty. The next
    insert then finds no conflict and makes the row afresh.
    """
    for _ in range(CLAIM_ATTEMPTS):
        await session.execute(
            postgresql.insert(Blob)
            .values(sha256=staged.sha256, size=staged.size, path=staged.path)
            .on_conflict_do_nothing(index_elements=[Blob.sha256])
        )
        blob = await session.scalar(
            select(Blob)
            .where(Blob.sha256 == staged.sha256)
            .with_for_update(read=True)
            .execution_options(populate_existing=True)
        )
        if blob is not None:
            return blob
    raise RuntimeError(f"No blob row for {staged.sha256} after {CLAIM_ATTEMPTS} attempts")


async def collect_garbage(session: AsyncSession, blob_ids: set[UUID]) -> None:
    """Delete the rows of the blobs among these that nothing points at any more.

    Rows only. The bytes stay on disk until :func:`sweep` finds a digest with
    no row: unlinking here, inside the request's transaction, removed content
    that a rollback then restored a row for, or that an upload racing this
    delete had just been promised (CYLIST-129). An unreferenced file costs disk
    until the next sweep; a row promising content that is gone costs a file.

    The rows are locked ``FOR UPDATE`` before their referrers are counted, so
    an upload holding one ``FOR SHARE`` — about to point a new row at it — is
    waited for and then counted, rather than missed and left to fail on the
    foreign key. In id order, so two deletes sharing blobs cannot deadlock.
    """
    if not blob_ids:
        return

    candidates = set(
        await session.scalars(
            select(Blob.id).where(Blob.id.in_(blob_ids)).order_by(Blob.id).with_for_update()
        )
    )
    if not candidates:
        return

    still_referenced: set[UUID] = set()
    for column in REFERRERS:
        held = await session.scalars(select(column).where(column.in_(candidates)))
        still_referenced.update(blob_id for blob_id in held if blob_id is not None)

    orphaned = candidates - still_referenced
    if orphaned:
        await session.execute(delete(Blob).where(Blob.id.in_(orphaned)))
        await session.flush()


@dataclass(frozen=True, slots=True)
class Swept:
    """What one :func:`sweep` took off the disk."""

    orphans: int
    """Published blobs no row referred to."""

    incoming: int
    """Uploads abandoned between staging and publishing."""


async def sweep(
    database: Database, store: BlobStore, *, idle_for: timedelta = SWEEP_IDLE_FOR
) -> Swept:
    """Remove bytes nothing refers to, and uploads nobody finished.

    The only place blob bytes are ever deleted. Each digest with no row is
    removed under its exclusive lock, which waits for no one: an upload holding
    the shared lock is about to give it a row, so it is skipped this pass.
    With the lock held the row is looked for again, since the first look was
    without it.

    A transaction per digest rather than one for the pass, because advisory
    locks live in the same fixed table as every other lock and a pass over a
    few thousand orphans must not hold a few thousand of them. Nothing is
    written, so there is nothing for a rollback to restore a row for.
    """
    on_disk = await store.held(idle_for=idle_for)
    orphans = 0
    if on_disk:
        async with database.session() as session:
            recorded = set(await session.scalars(select(Blob.sha256)))
        for sha256 in sorted(on_disk.keys() - recorded):
            async with database.session() as session:
                taken = await session.scalar(
                    select(func.pg_try_advisory_xact_lock(*digest_lock(sha256)))
                )
                if not taken:
                    continue
                if await session.scalar(select(Blob.id).where(Blob.sha256 == sha256)) is not None:
                    continue
                await store.remove(on_disk[sha256])
                orphans += 1
    incoming = await store.clear_incoming(idle_for=idle_for)
    if orphans or incoming:
        logger.info(
            "Swept %d unreferenced blob(s) and %d abandoned upload(s)",
            orphans,
            incoming,
            extra={"context": {"orphans": orphans, "incoming": incoming}},
        )
    return Swept(orphans=orphans, incoming=incoming)


async def sweep_if_first(database: Database, store: BlobStore) -> Swept | None:
    """One periodic :func:`sweep`, unless another process is already doing one.

    Every process runs the loop; the pass that takes :data:`SWEEPER_LEASE`
    sweeps and the rest skip. Not for correctness — two sweeps contend per
    digest and are safe — but for the disk: a walk of every blob is not
    something to do once per worker.
    """
    async with database.session() as lease:
        if not await lease.scalar(select(func.pg_try_advisory_xact_lock(SWEEPER_LEASE))):
            return None
        # Named rather than left to the default, which was fixed when this
        # module loaded.
        return await sweep(database, store, idle_for=SWEEP_IDLE_FOR)


async def ids_on_project(session: AsyncSession, project_id: UUID) -> set[UUID]:
    """Every blob this project's rows point at, read before those rows go.

    Beside :data:`REFERRERS` because it is the same fact asked a different way,
    and keeping the two apart is how one of them gets forgotten: a new table
    that can hold a blob needs an entry there *and* a clause here.

    Not derived from ``REFERRERS`` itself, because the two referrers reach a
    project differently — a file item knows its folder and the folder knows the
    project, while a skill says so on its own row — and a generic join over a
    column alone cannot know which.
    """
    filed = await session.scalars(
        select(FileItem.blob_id)
        .join(Folder, Folder.id == FileItem.folder_id)
        .where(Folder.project_id == project_id, FileItem.blob_id.is_not(None))
    )
    from_skills = await session.scalars(select(Skill.blob_id).where(Skill.project_id == project_id))
    return {blob_id for blob_id in (*filed, *from_skills) if blob_id is not None}
