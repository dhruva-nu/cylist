"""A project's file tree: folders, uploads and links.

Two rules run through everything here.

*Names are unique within their folder.* A directory holding two things called
``report.pdf`` is a bug waiting to be reported as data loss. The database
enforces it; this module checks first so the caller gets a 409 instead of an
integrity error, and — for uploads — so the bytes are never written at all.

*Bytes are shared, rows are not.* Two items pointing at the same content share
one :class:`~app.models.file.Blob`, so deleting an item deletes the content
only once nothing else refers to it.

*Every project has a root folder, and it is the project.* Omitting ``parent_id``
means "in the root" rather than "nowhere", so a file can sit at the top of a
project without anybody inventing a folder for it. The root itself cannot be
renamed, moved or deleted — see :func:`_ensure_not_the_root`.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import NamedTuple
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import Select, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import (
    ConflictError,
    NotFoundError,
    PayloadTooLargeError,
    UnprocessableRequestError,
)
from app.models.file import Blob, FileItem, Folder, ItemKind, ItemSource
from app.models.person import Person
from app.models.project import Project
from app.schemas.files import FolderCreate, FolderUpdate, ItemUpdate, LinkCreate, clean_name
from app.storage import BlobStore, BlobTooLargeError

CHUNK_BYTES = 1 << 20
"""How much of an upload is held in memory at once. Starlette has already
spooled anything past its own megabyte to a temporary file, so this bounds the
copy from there into the blob store rather than the receive itself."""

MAX_DEPTH = 32
"""How deep a folder may sit, the root counting as the first level. A cap
rather than a guess: it makes every walk up the tree terminate, so a cycle that
somehow reached the database surfaces as a 422 instead of a hung request."""

DEFAULT_MIME = "application/octet-stream"


class Counts(NamedTuple):
    """What a project holds, for its hub card."""

    folders: int
    items: int


# --- The root --------------------------------------------------------------


async def seed_root(session: AsyncSession, project: Project) -> Folder:
    """Give a new project the one folder it will never be without.

    Created with the project, the way its starter columns are: a project whose
    tree begins empty has nowhere to put a README, and asking someone to make a
    folder before they can upload anything is a step with no purpose.
    """
    root = Folder(project_id=project.id, parent_id=None, name=project.name)
    session.add(root)
    await session.flush()
    return root


async def root_of(session: AsyncSession, project_id: UUID) -> Folder:
    """The project's root folder.

    Raises:
        UnprocessableRequestError: if the project has none, which only a
            database edited by hand can produce.
    """
    root = await session.scalar(
        select(Folder).where(Folder.project_id == project_id, Folder.parent_id.is_(None))
    )
    if root is None:
        raise UnprocessableRequestError(
            "This project has no root folder to file anything under.",
            details={"project_id": str(project_id)},
        )
    return root


async def rename_root(session: AsyncSession, project: Project) -> None:
    """Point the root at the project's current name.

    The root row carries the name rather than deriving it, so that a folder
    always describes itself — the tree, the breadcrumb and any future export
    need no join. Renaming it here, in the same transaction as the project, is
    what stops the copy drifting.
    """
    root = await root_of(session, project.id)
    root.name = project.name
    await session.flush()


# --- Folders ---------------------------------------------------------------


async def create_folder(session: AsyncSession, project: Project, data: FolderCreate) -> Folder:
    """Add a folder, in the project's root or inside another.

    Raises:
        ConflictError: if the parent already has a folder with this name.
        NotFoundError: if ``parent_id`` names no folder.
        UnprocessableRequestError: if the parent is in a different project, or
            is already as deep as folders may go.
    """
    parent = await _destination(session, project.id, data.parent_id)
    await _ensure_depth_allows_a_child(session, parent)

    folder = Folder(project_id=project.id, parent_id=parent.id, name=data.name)
    session.add(folder)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate_name(data.name) from exc
    return folder


async def get_folder(session: AsyncSession, folder_id: UUID) -> Folder:
    """Fetch one folder."""
    folder = await session.get(Folder, folder_id)
    if folder is None:
        raise NotFoundError("No folder with that id.")
    return folder


async def list_folders(session: AsyncSession, project: Project) -> list[Folder]:
    """Every folder in the project, alphabetically.

    The whole tree in one query: the files screen needs all of it to draw its
    left-hand pane, and the alternative is a request per expanded node.
    """
    return list(
        await session.scalars(
            select(Folder).where(Folder.project_id == project.id).order_by(Folder.name)
        )
    )


async def children(session: AsyncSession, folder: Folder) -> tuple[list[Folder], list[FileItem]]:
    """The subfolders and items directly inside a folder, each alphabetical."""
    subfolders = list(
        await session.scalars(
            select(Folder).where(Folder.parent_id == folder.id).order_by(Folder.name)
        )
    )
    items = list(
        await session.scalars(
            select(FileItem).where(FileItem.folder_id == folder.id).order_by(FileItem.name)
        )
    )
    return subfolders, items


async def path_to(session: AsyncSession, folder: Folder) -> list[Folder]:
    """The breadcrumb: every folder above this one, outermost first, then it."""
    ancestors = await _ancestors(session, folder)
    return [*reversed(ancestors), folder]


async def update_folder(session: AsyncSession, folder: Folder, data: FolderUpdate) -> Folder:
    """Rename a folder, move it, or both.

    Raises:
        ConflictError: if the destination already has a folder with this name.
        UnprocessableRequestError: if the folder is the project's root, or if
            the move would put the folder inside its own subtree or past the
            depth limit.
    """
    _ensure_not_the_root(folder, "renamed or moved")
    fields = data.model_dump(exclude_unset=True)

    if "parent_id" in fields:
        parent = await _destination(session, folder.project_id, fields["parent_id"])
        await _ensure_not_inside_itself(session, folder, parent)
        await _ensure_depth_allows_a_child(session, parent)
        folder.parent_id = parent.id

    if fields.get("name") is not None:
        folder.name = fields["name"]

    # Read now, not in the handler below: a failed flush leaves the session
    # unusable, and every attribute read on it would try to reload the row.
    name = folder.name
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate_name(name) from exc
    return folder


async def delete_folder(session: AsyncSession, store: BlobStore, folder: Folder) -> None:
    """Delete a folder and everything under it.

    The subtree goes by database cascade rather than by a walk in Python, so an
    interrupted delete cannot leave a branch pointing at a parent that is gone.
    What Python still has to do is decide which blobs that orphaned.

    Raises:
        UnprocessableRequestError: if the folder is the project's root.
    """
    _ensure_not_the_root(folder, "deleted")
    blob_ids = await _blob_ids_under(session, folder)
    await session.execute(delete(Folder).where(Folder.id == folder.id))
    await session.flush()
    await _collect_garbage(session, store, blob_ids)


# --- Items -----------------------------------------------------------------


async def upload(
    session: AsyncSession,
    store: BlobStore,
    folder: Folder,
    source: UploadFile,
    *,
    max_bytes: int,
    added_by: UUID | None,
) -> FileItem:
    """Store an uploaded file in a folder.

    The name is checked before a single byte is written: an upload that is
    going to be refused for a duplicate name should not first be copied onto
    the disk. The content itself is written straight through in chunks, so a
    file over the cap is refused part-way rather than after it has all arrived.

    Raises:
        ConflictError: if the folder already has an item with this name.
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
        UnprocessableRequestError: if the upload has no usable filename, or
            ``added_by`` names nobody in the directory.
    """
    name = _filename_of(source)
    await _ensure_name_is_free(session, folder, name)
    await _ensure_person_exists(session, added_by)

    try:
        stored = await store.write(_chunks(source), max_bytes=max_bytes)
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

    return await _add_item(
        session,
        FileItem(
            folder_id=folder.id,
            kind=ItemKind.FILE,
            name=name,
            blob_id=blob.id,
            source=ItemSource.UPLOAD,
            size=stored.size,
            mime=mime,
            added_by=added_by,
        ),
    )


async def add_link(session: AsyncSession, folder: Folder, data: LinkCreate) -> FileItem:
    """Put a link to somebody else's document in a folder.

    Raises:
        ConflictError: if the folder already has an item with this name.
        UnprocessableRequestError: if the source claims the file was uploaded
            here, or ``added_by`` names nobody in the directory.
    """
    if data.source is ItemSource.UPLOAD:
        raise UnprocessableRequestError(
            "A link's source is where the document actually lives, so it cannot be `upload`.",
            details={"source": data.source.value},
        )
    await _ensure_name_is_free(session, folder, data.name)
    await _ensure_person_exists(session, data.added_by)

    return await _add_item(
        session,
        FileItem(
            folder_id=folder.id,
            kind=ItemKind.LINK,
            name=data.name,
            url=data.url,
            source=data.source,
            added_by=data.added_by,
        ),
    )


async def get_item(session: AsyncSession, item_id: UUID) -> FileItem:
    """Fetch one file or link."""
    item = await session.get(FileItem, item_id)
    if item is None:
        raise NotFoundError("No file or link with that id.")
    return item


async def update_item(session: AsyncSession, item: FileItem, data: ItemUpdate) -> FileItem:
    """Rename an item, or correct a link's target.

    An item's kind never changes: a link that turned out to be a file is a new
    upload, not an edit, because the two have nothing in common but a name.

    Raises:
        ConflictError: if the folder already has an item with the new name.
        UnprocessableRequestError: if a URL or source is offered for an
            uploaded file, or a link is told it was uploaded here.
    """
    fields = data.model_dump(exclude_unset=True)

    if item.kind is ItemKind.FILE and (fields.get("url") is not None or "source" in fields):
        raise UnprocessableRequestError(
            "An uploaded file's URL and source are fixed by the upload."
        )
    if fields.get("source") is ItemSource.UPLOAD:
        raise UnprocessableRequestError(
            "A link's source is where the document actually lives, so it cannot be `upload`."
        )
    if "added_by" in fields:
        await _ensure_person_exists(session, fields["added_by"])

    if fields.get("name") is not None:
        item.name = fields["name"]
    if fields.get("url") is not None:
        item.url = fields["url"]
    if fields.get("source") is not None:
        item.source = fields["source"]
    if "added_by" in fields:
        # Null clears it, which is the only way to undo a wrong attribution.
        item.added_by = fields["added_by"]

    name = item.name
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate_name(name) from exc

    await session.refresh(item, ["added_by_person"])
    return item


async def delete_item(session: AsyncSession, store: BlobStore, item: FileItem) -> None:
    """Remove a file or link, and its content if nothing else refers to it."""
    blob_ids = {item.blob_id} if item.blob_id is not None else set()
    await session.delete(item)
    await session.flush()
    await _collect_garbage(session, store, blob_ids)


async def counts(session: AsyncSession, project: Project) -> Counts:
    """How many folders and items the project holds, for its hub card.

    The root is not counted. Every project has one and nobody can remove it, so
    counting it would report "1 folder" for a project nothing has been filed in
    yet.
    """
    in_project = select(Folder.id).where(Folder.project_id == project.id)
    folders = await session.scalar(
        select(func.count())
        .select_from(Folder)
        .where(Folder.project_id == project.id, Folder.parent_id.is_not(None))
    )
    items = await session.scalar(
        select(func.count()).select_from(FileItem).where(FileItem.folder_id.in_(in_project))
    )
    return Counts(folders=folders or 0, items=items or 0)


# --- Internals -------------------------------------------------------------


async def _chunks(source: UploadFile, size: int = CHUNK_BYTES) -> AsyncIterator[bytes]:
    """Read an upload a chunk at a time.

    Deliberately not ``await source.read()``: that would pull however many
    hundred megabytes the client sent into memory, only to hand them to a store
    that is going to write them out again anyway.
    """
    while chunk := await source.read(size):
        yield chunk


def _filename_of(source: UploadFile) -> str:
    """The name to file an upload under, stripped of any directory part.

    Browsers send a bare filename, but a directory upload or a hand-rolled
    client can send a path, and ``Downloads/../../etc/passwd`` is not a name.
    """
    candidate = (source.filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    try:
        return clean_name(candidate)
    except ValueError as exc:
        raise UnprocessableRequestError(
            "That upload has no usable filename.", details={"filename": source.filename}
        ) from exc


async def _add_item(session: AsyncSession, item: FileItem) -> FileItem:
    """Insert an item, turning a lost race for its name into a 409."""
    name = item.name
    session.add(item)
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate_name(name) from exc

    # Freshly inserted rows have no loaded relationship, and reading one
    # outside a query would mean lazy IO on the event loop.
    await session.refresh(item, ["added_by_person"])
    return item


def _duplicate_name(name: str) -> ConflictError:
    return ConflictError(f"Something called {name!r} is already here.", details={"name": name})


async def _ensure_name_is_free(session: AsyncSession, folder: Folder, name: str) -> None:
    taken = await session.scalar(
        select(FileItem.id).where(FileItem.folder_id == folder.id, FileItem.name == name)
    )
    if taken is not None:
        raise _duplicate_name(name)


async def _ensure_person_exists(session: AsyncSession, person_id: UUID | None) -> None:
    if person_id is None:
        return
    if await session.get(Person, person_id) is None:
        raise UnprocessableRequestError(
            "That person is not in the directory.", details={"person_id": str(person_id)}
        )


def _ensure_not_the_root(folder: Folder, attempted: str) -> None:
    """Refuse a change that would unmake a project's root.

    Phrased as an explanation rather than a refusal: the root was not created
    by mistake and there is nothing wrong with wanting to rename it, so the
    useful thing to say is where the name actually comes from.
    """
    if folder.is_root:
        raise UnprocessableRequestError(
            f"The root folder stands for the project itself, so it cannot be {attempted}. "
            "Rename the project to change what it is called, or delete the folders "
            "inside it to empty it.",
            details={"folder_id": str(folder.id), "project_id": str(folder.project_id)},
        )


async def _destination(session: AsyncSession, project_id: UUID, parent_id: UUID | None) -> Folder:
    """The folder something is going into — the project's root when none is named.

    No caller may end up with ``None`` here. A second parentless folder would
    be a second root, and while the unique index refuses that, it refuses it as
    an integrity error rather than as an answer.
    """
    if parent_id is None:
        return await root_of(session, project_id)

    parent = await get_folder(session, parent_id)
    if parent.project_id != project_id:
        raise UnprocessableRequestError(
            "That folder belongs to a different project.",
            details={"parent_id": str(parent_id)},
        )
    return parent


async def _ancestors(session: AsyncSession, folder: Folder) -> list[Folder]:
    """Every folder above this one, nearest first."""
    chain: list[Folder] = []
    cursor = folder.parent_id
    while cursor is not None:
        if len(chain) >= MAX_DEPTH:
            raise UnprocessableRequestError(
                f"That folder is nested more than {MAX_DEPTH} deep.",
                details={"folder_id": str(folder.id)},
            )
        parent = await get_folder(session, cursor)
        chain.append(parent)
        cursor = parent.parent_id
    return chain


async def _ensure_not_inside_itself(session: AsyncSession, folder: Folder, parent: Folder) -> None:
    """Refuse a move that would make a folder its own ancestor.

    Left to the database this would succeed and quietly detach the whole
    subtree from the project: a ring of folders parented to each other is
    reachable from nothing and deletable by nothing.
    """
    lineage = [parent, *await _ancestors(session, parent)]
    if any(ancestor.id == folder.id for ancestor in lineage):
        raise UnprocessableRequestError(
            "A folder cannot be moved inside itself.",
            details={"folder_id": str(folder.id), "parent_id": str(parent.id)},
        )


async def _ensure_depth_allows_a_child(session: AsyncSession, parent: Folder) -> None:
    if len(await _ancestors(session, parent)) + 1 >= MAX_DEPTH:
        raise UnprocessableRequestError(
            f"Folders cannot be nested more than {MAX_DEPTH} deep.",
            details={"parent_id": str(parent.id)},
        )


def _subtree_of(folder: Folder) -> Select[tuple[UUID]]:
    """Select the ids of a folder and every folder beneath it."""
    subtree = select(Folder.id).where(Folder.id == folder.id).cte("subtree", recursive=True)
    subtree = subtree.union_all(select(Folder.id).join(subtree, Folder.parent_id == subtree.c.id))
    return select(subtree.c.id)


async def _blob_ids_under(session: AsyncSession, folder: Folder) -> set[UUID]:
    """Which blobs the folder's subtree refers to, before it is deleted."""
    return set(
        await session.scalars(
            select(FileItem.blob_id).where(
                FileItem.folder_id.in_(_subtree_of(folder)),
                FileItem.blob_id.is_not(None),
            )
        )
    )


async def _collect_garbage(session: AsyncSession, store: BlobStore, blob_ids: set[UUID]) -> None:
    """Delete the blobs among these that nothing points at any more.

    Rows go before bytes. The reverse order would, if the transaction were
    rolled back afterwards, leave a row promising content that is no longer
    there; this way the worst case is an unreferenced file on disk, which
    nothing can reach and which the next upload of the same content replaces.
    """
    if not blob_ids:
        return

    still_referenced = set(
        await session.scalars(select(FileItem.blob_id).where(FileItem.blob_id.in_(blob_ids)))
    )
    orphaned = list(
        await session.scalars(select(Blob).where(Blob.id.in_(blob_ids - still_referenced)))
    )
    if not orphaned:
        return

    await session.execute(delete(Blob).where(Blob.id.in_([blob.id for blob in orphaned])))
    await session.flush()
    for blob in orphaned:
        await store.remove(blob.path)
