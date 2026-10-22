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

from collections.abc import Mapping
from typing import NamedTuple
from uuid import UUID

from sqlalchemy import Select, delete, func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import multipart
from app.core.errors import ConflictError, NotFoundError, UnprocessableRequestError
from app.core.sensitivity import Sensitivity
from app.models.file import Blob, FileItem, Folder, ItemKind, ItemSource
from app.models.person import Person
from app.models.project import Project
from app.schemas.files import FolderCreate, FolderUpdate, ItemUpdate, LinkCreate, clean_name
from app.services import blobs, permissions
from app.services.paging import PageRequest, Window, keyset
from app.storage import BlobStore

MAX_DEPTH = 32
"""How deep a folder may sit, the root counting as the first level. A cap
rather than a guess: it makes every walk up the tree terminate, so a cycle that
somehow reached the database surfaces as a 422 instead of a hung request."""

_LINK_CANNOT_BE_UPLOAD = (
    "A link's source is where the document actually lives, so it cannot be `upload`."
)


class Counts(NamedTuple):
    """What a project holds, for its hub card."""

    folders: int
    items: int


class Filed(NamedTuple):
    """One item, and the folders it sits under."""

    item: FileItem

    folder_path: str
    """The folders between the root and this item, outermost first, joined by
    ``/`` — ``"Contracts/2026"``, and empty for an item at the top of the
    project. The root is left out because it *is* the project: repeating the
    project's name in front of every path would say nothing."""


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

    folder = Folder(
        project_id=project.id,
        parent_id=parent.id,
        name=data.name,
        # Inherited rather than defaulted: a subfolder of Contracts is part of
        # Contracts, and making the caller restate it is how a folder ends up
        # more open than the one it sits in.
        default_sensitivity=(data.default_sensitivity or parent.default_sensitivity),
    )
    session.add(folder)
    await _flush_refusing_duplicate(session, data.name)
    return folder


async def folder_in_root(session: AsyncSession, project: Project, name: str) -> Folder:
    """The folder called ``name`` at the top of the project, made if it is not there.

    For a folder Cylist files things in itself — ``Cold storage`` — which should
    be found where somebody would look for it, and reused rather than made
    again every time: one folder of exports, not one per export.
    """
    root = await root_of(session, project.id)
    found = await session.scalar(
        select(Folder).where(Folder.parent_id == root.id, Folder.name == name)
    )
    if found is not None:
        return found
    return await create_folder(session, project, FolderCreate(name=name, parent_id=root.id))


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


async def folder_page(session: AsyncSession, project: Project, page: PageRequest) -> Window[Folder]:
    """A page of the project's folders, alphabetically."""
    return await keyset(
        session,
        select(Folder).where(Folder.project_id == project.id),
        keys=(Folder.name, Folder.id),
        page=page,
    )


async def list_items(
    session: AsyncSession,
    project: Project,
    page: PageRequest,
    *,
    allowed: frozenset[Sensitivity],
) -> tuple[list[Filed], UUID | None]:
    """A page of the files and links in the project, flat, each with where it
    is filed — and the cursor for the next page.

    ``allowed`` is the levels the caller may read, applied in the query so that
    a page is a full page of what the caller may see rather than a page with
    the rest struck out of it.

    The tree flattened rather than walked: this answers "what files does this
    project have", which is the question a picker asks — the `>` tag in a
    description or a comment offers these names, and it offers all of them at
    once because a name is how a file is referred to there, not a path.

    Two queries whatever the shape of the tree. The path is assembled here from
    the folder rows rather than asked of the database per item: the tree is
    small, bounded by :data:`MAX_DEPTH`, and a recursive CTE would be a lot of
    SQL to answer a question Python answers with a dictionary.
    """
    folders = await list_folders(session, project)
    paths = _paths_of(folders)
    window = await keyset(
        session,
        select(FileItem).where(
            FileItem.folder_id.in_([folder.id for folder in folders]),
            permissions.readable_clause(FileItem.sensitivity, allowed),
        ),
        # Folded rather than left to the database's collation: this is a
        # list somebody reads down looking for a name, and "Tax portal"
        # belongs between "readme.md" and "vat.xlsx" wherever it runs.
        keys=(func.lower(FileItem.name), FileItem.name, FileItem.id),
        page=page,
    )
    return [
        Filed(item=item, folder_path=paths.get(item.folder_id, "")) for item in window.rows
    ], window.next


async def subfolders(session: AsyncSession, folder: Folder) -> list[Folder]:
    """The folders directly inside a folder, alphabetical.

    Whole rather than paged: folders are the tree's structure, which
    :func:`list_folders` already hands out entire for the files screen's
    left-hand pane.
    """
    return list(
        await session.scalars(
            select(Folder).where(Folder.parent_id == folder.id).order_by(Folder.name)
        )
    )


async def items_in(
    session: AsyncSession,
    folder: Folder,
    page: PageRequest,
    *,
    allowed: frozenset[Sensitivity],
) -> Window[FileItem]:
    """A page of the items directly inside a folder that the caller may see,
    alphabetical."""
    return await keyset(
        session,
        select(FileItem).where(
            FileItem.folder_id == folder.id,
            permissions.readable_clause(FileItem.sensitivity, allowed),
        ),
        keys=(FileItem.name, FileItem.id),
        page=page,
    )


async def path_to(session: AsyncSession, folder: Folder) -> list[Folder]:
    """The breadcrumb: every folder above this one, outermost first, then it."""
    ancestors = await _ancestors(session, folder)
    return [*reversed(ancestors), folder]


async def update_folder(session: AsyncSession, folder: Folder, data: FolderUpdate) -> Folder:
    """Rename a folder, move it, or both.

    Also where a folder's default classification is set, which is the one edit
    the project's root accepts — see the note below.

    Raises:
        ConflictError: if the destination already has a folder with this name.
        UnprocessableRequestError: if the folder is the project's root and is
            being renamed or moved, or if the move would put the folder inside
            its own subtree or past the depth limit.
    """
    fields = data.model_dump(exclude_unset=True)
    # Only for the two the root actually refuses. Its default classification
    # is not one of them: the root is where most of a project's files land, so
    # a project that wanted everything restricted by default and could not say
    # so at the top would have to say it on every folder instead.
    if fields.keys() & {"name", "parent_id"}:
        _ensure_not_the_root(folder, "renamed or moved")

    if "parent_id" in fields:
        parent = await _destination(session, folder.project_id, fields["parent_id"])
        await _ensure_not_inside_itself(session, folder, parent)
        await _ensure_depth_allows_a_child(session, parent)
        folder.parent_id = parent.id

    if fields.get("name") is not None:
        folder.name = fields["name"]
    if fields.get("default_sensitivity") is not None:
        folder.default_sensitivity = fields["default_sensitivity"]

    await _flush_refusing_duplicate(session, folder.name)
    return folder


async def delete_folder(session: AsyncSession, folder: Folder) -> None:
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
    await blobs.collect_garbage(session, blob_ids)


# --- Items -----------------------------------------------------------------


async def upload(
    session: AsyncSession,
    store: BlobStore,
    folder: Folder,
    form: multipart.Form,
    *,
    max_bytes: int,
) -> FileItem:
    """Store the file a ``multipart/form-data`` body carries in a folder.

    The form is read here, part by part, after the route's permission checks
    have run (CYLIST-123): ``file`` is the file, ``added_by`` and
    ``sensitivity`` are optional fields beside it.

    The name is checked before a single byte is written: an upload that is
    going to be refused for a duplicate name should not first be copied onto
    the disk — and nor should one naming nobody as its uploader, if the form
    says so before the file. The content goes from the socket to the blob
    store in chunks, so a file over the cap is refused part-way rather than
    after it has all arrived.

    Raises:
        ConflictError: if the folder already has an item with this name.
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
        RequestValidationError: if there is no file, more than one, or a
            field that does not parse.
        UnprocessableRequestError: if the upload has no usable filename, or
            ``added_by`` names nobody in the directory.
    """
    stored: list[tuple[str, Blob, str]] = []

    async def take(part: multipart.Part, before: Mapping[str, list[str]]) -> None:
        if stored:
            raise multipart.invalid_field("file", "send one file")
        name = filename_of(part.filename)
        await _ensure_name_is_free(session, folder, name)
        await ensure_person_exists(session, _added_by(before))
        blob = await blobs.store_stream(session, store, part.chunks(), max_bytes=max_bytes)
        stored.append((name, blob, blobs.declared_mime(part.content_type)))

    fields = await form.read(file_field="file", on_file=take, text_fields=_UPLOAD_FIELDS)
    if not stored:
        raise multipart.invalid_field("file", "Field required", kind="missing")
    name, blob, mime = stored[0]
    added_by = _added_by(fields)
    await ensure_person_exists(session, added_by)

    return await _add_item(
        session,
        FileItem(
            folder_id=folder.id,
            kind=ItemKind.FILE,
            name=name,
            blob=blob,
            source=ItemSource.UPLOAD,
            mime=mime,
            added_by=added_by,
            sensitivity=_sensitivity(fields) or folder.default_sensitivity,
        ),
    )


_UPLOAD_FIELDS = frozenset({"added_by", "sensitivity"})


def _added_by(fields: Mapping[str, list[str]]) -> UUID | None:
    value = multipart.single(fields, "added_by")
    if value is None:
        return None
    try:
        return UUID(value)
    except ValueError as exc:
        raise multipart.invalid_field("added_by", "must be a UUID", kind="uuid_parsing") from exc


def _sensitivity(fields: Mapping[str, list[str]]) -> str | None:
    value = multipart.single(fields, "sensitivity")
    if value is None:
        return None
    try:
        return Sensitivity(value).value
    except ValueError as exc:
        levels = ", ".join(level.value for level in Sensitivity)
        raise multipart.invalid_field(
            "sensitivity", f"must be one of {levels}", kind="enum"
        ) from exc


async def store_generated(
    session: AsyncSession,
    store: BlobStore,
    folder: Folder,
    *,
    name: str,
    data: bytes,
    mime: str,
    max_bytes: int,
    added_by: UUID | None,
) -> FileItem:
    """File bytes the server wrote itself, as though they had been uploaded.

    For an export Cylist makes on somebody's behalf — cold storage's CSV — which
    belongs in the folder listing beside everything people uploaded, and is
    downloaded, classified and deleted the same way. It is an upload by the
    person who asked for it, so ``source`` says so.

    Raises:
        ConflictError: if the folder already has an item with this name.
        PayloadTooLargeError: if the content exceeds ``max_bytes``.
    """
    await _ensure_name_is_free(session, folder, name)
    await ensure_person_exists(session, added_by)
    blob = await blobs.store_bytes(session, store, data, max_bytes=max_bytes)
    return await _add_item(
        session,
        FileItem(
            folder_id=folder.id,
            kind=ItemKind.FILE,
            name=name,
            blob=blob,
            source=ItemSource.UPLOAD,
            mime=mime,
            added_by=added_by,
            sensitivity=folder.default_sensitivity,
        ),
    )


async def name_is_free(session: AsyncSession, folder: Folder, name: str) -> bool:
    """Whether nothing in this folder is called ``name`` yet."""
    taken = await session.scalar(
        select(FileItem.id).where(FileItem.folder_id == folder.id, FileItem.name == name)
    )
    return taken is None


async def add_link(session: AsyncSession, folder: Folder, data: LinkCreate) -> FileItem:
    """Put a link to somebody else's document in a folder.

    Raises:
        ConflictError: if the folder already has an item with this name.
        UnprocessableRequestError: if the source claims the file was uploaded
            here, or ``added_by`` names nobody in the directory.
    """
    if data.source is ItemSource.UPLOAD:
        raise UnprocessableRequestError(
            _LINK_CANNOT_BE_UPLOAD, details={"source": data.source.value}
        )
    await _ensure_name_is_free(session, folder, data.name)
    await ensure_person_exists(session, data.added_by)

    return await _add_item(
        session,
        FileItem(
            folder_id=folder.id,
            kind=ItemKind.LINK,
            name=data.name,
            url=data.url,
            source=data.source,
            added_by=data.added_by,
            sensitivity=(data.sensitivity or folder.default_sensitivity),
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
        raise UnprocessableRequestError(_LINK_CANNOT_BE_UPLOAD)
    if "added_by" in fields:
        await ensure_person_exists(session, fields["added_by"])

    if fields.get("sensitivity") is not None:
        item.sensitivity = fields["sensitivity"]
    if fields.get("name") is not None:
        item.name = fields["name"]
    if fields.get("url") is not None:
        item.url = fields["url"]
    if fields.get("source") is not None:
        item.source = fields["source"]
    if "added_by" in fields:
        # Null clears it, which is the only way to undo a wrong attribution.
        item.added_by = fields["added_by"]

    await _flush_refusing_duplicate(session, item.name)
    await session.refresh(item, ["added_by_person"])
    return item


async def project_id_of_item(session: AsyncSession, item: FileItem) -> UUID:
    """Which project a file or link is on, by way of the folder holding it.

    A file knows its folder and nothing above it — the tree is what has a
    project — so this is the hop anything checking a permission on one makes.
    """
    folder = await get_folder(session, item.folder_id)
    return folder.project_id


async def delete_item(session: AsyncSession, item: FileItem) -> None:
    """Remove a file or link, and its content if nothing else refers to it."""
    blob_ids = {item.blob_id} if item.blob_id is not None else set()
    await session.delete(item)
    await session.flush()
    await blobs.collect_garbage(session, blob_ids)


async def counts(
    session: AsyncSession, project: Project, *, readable: frozenset[Sensitivity] | None = None
) -> Counts:
    """How many folders and items the project holds, for its hub card.

    The root is not counted. Every project has one and nobody can remove it, so
    counting it would report "1 folder" for a project nothing has been filed in
    yet.

    Args:
        readable: The levels the reader is cleared for. Items above them are
            left out — a count is a listing summed up, and one that said "11
            files" to somebody who can only reach ten would have told them a
            file exists, which is the whole thing levels are for. ``None``
            counts everything, for the callers that are not answering a person.
    """
    in_project = select(Folder.id).where(Folder.project_id == project.id)
    folders = await session.scalar(
        select(func.count())
        .select_from(Folder)
        .where(Folder.project_id == project.id, Folder.parent_id.is_not(None))
    )
    counting = select(func.count()).select_from(FileItem).where(FileItem.folder_id.in_(in_project))
    if readable is not None:
        counting = counting.where(FileItem.sensitivity.in_([level.value for level in readable]))
    items = await session.scalar(counting)
    return Counts(folders=folders or 0, items=items or 0)


# --- Internals -------------------------------------------------------------


def filename_of(filename: str | None) -> str:
    """The name to file an upload under, stripped of any directory part.

    Browsers send a bare filename, but a directory upload or a hand-rolled
    client can send a path, and ``Downloads/../../etc/passwd`` is not a name.

    A skill upload is named by this too (see :mod:`app.services.agents`): what
    is a name rather than a path does not differ because the row it lands in is
    a different table.
    """
    candidate = (filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    try:
        return clean_name(candidate)
    except ValueError as exc:
        raise UnprocessableRequestError(
            "That upload has no usable filename.", details={"filename": filename}
        ) from exc


async def _add_item(session: AsyncSession, item: FileItem) -> FileItem:
    """Insert an item, turning a lost race for its name into a 409."""
    session.add(item)
    await _flush_refusing_duplicate(session, item.name)

    # Freshly inserted rows have no loaded relationship, and reading one
    # outside a query would mean lazy IO on the event loop.
    await session.refresh(item, ["added_by_person"])
    return item


async def _flush_refusing_duplicate(session: AsyncSession, name: str) -> None:
    """Flush, turning the unique index on names-in-a-folder into a 409.

    ``name`` is taken from the caller before the flush rather than read off
    the row after it: a failed flush leaves the session unusable, and every
    attribute read on it would try to reload the row.
    """
    try:
        await session.flush()
    except IntegrityError as exc:
        raise _duplicate_name(name) from exc


def _duplicate_name(name: str) -> ConflictError:
    return ConflictError(f"Something called {name!r} is already here.", details={"name": name})


async def _ensure_name_is_free(session: AsyncSession, folder: Folder, name: str) -> None:
    if not await name_is_free(session, folder, name):
        raise _duplicate_name(name)


async def ensure_person_exists(session: AsyncSession, person_id: UUID | None) -> None:
    """Refuse an attribution to somebody who is not in the directory.

    Checked here rather than left to the foreign key, so the caller hears which
    id was wrong instead of getting an integrity error. ``None`` is always
    fine: nobody has to be named as having added something.
    """
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


def _paths_of(folders: list[Folder]) -> dict[UUID, str]:
    """Where each folder sits, keyed by its id — see :attr:`Filed.folder_path`.

    Walks up from each folder rather than down from the root, so a row whose
    parent is somehow missing costs itself and nothing else. :data:`MAX_DEPTH`
    bounds the walk, which is what makes a cycle in the data a short path
    instead of a hung request.
    """
    by_id = {folder.id: folder for folder in folders}
    paths: dict[UUID, str] = {}

    for folder in folders:
        names: list[str] = []
        current: Folder | None = folder
        for _ in range(MAX_DEPTH):
            if current is None or current.parent_id is None:
                break
            names.append(current.name)
            current = by_id.get(current.parent_id)
        paths[folder.id] = "/".join(reversed(names))

    return paths


async def _ancestors(session: AsyncSession, folder: Folder) -> list[Folder]:
    """Every folder above this one, nearest first.

    One query however deep the tree is: climbing a row at a time costs a round
    trip per level, which at :data:`MAX_DEPTH` is a request's worth of latency
    spent on every move and every create. The climb is cut one level past the
    limit, so a ring in the data is refused here rather than followed forever.
    """
    chain = (
        select(Folder.id, Folder.parent_id, literal(1).label("level"))
        .where(Folder.id == folder.parent_id)
        .cte("chain", recursive=True)
    )
    chain = chain.union_all(
        select(Folder.id, Folder.parent_id, chain.c.level + 1)
        .join(chain, Folder.id == chain.c.parent_id)
        .where(chain.c.level <= MAX_DEPTH)
    )
    ancestors = list(
        await session.scalars(
            select(Folder).join(chain, Folder.id == chain.c.id).order_by(chain.c.level)
        )
    )
    if len(ancestors) > MAX_DEPTH:
        raise UnprocessableRequestError(
            f"That folder is nested more than {MAX_DEPTH} deep.",
            details={"folder_id": str(folder.id)},
        )
    return ancestors


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
