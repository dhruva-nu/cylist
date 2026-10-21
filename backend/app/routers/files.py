"""A project's files: folders, uploads, links and downloads.

One router rather than three despite the three prefixes. Folders hang off a
project, but items and downloads are addressed by their own id — a file keeps
its URL when it is moved — so splitting by prefix would scatter one feature
across three modules for no gain.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, Request, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import current_principal, require
from app.auth.permissions import Permission
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core import multipart, sensitivity
from app.core.body_limit import streams_upload
from app.core.errors import NotFoundError, UnprocessableRequestError
from app.core.multipart import multipart_body
from app.core.security_headers import DOWNLOAD_HEADERS
from app.core.sensitivity import DEFAULT_LEVEL
from app.db import SessionDependency
from app.models.file import FileItem, Folder, ItemKind
from app.models.project import Project
from app.routers import guards
from app.routers.paging import PageQuery
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.files import (
    FiledItem,
    FiledItemPage,
    FolderChildren,
    FolderCreate,
    FolderCrumb,
    FolderNode,
    FolderPage,
    FolderRead,
    FolderUpdate,
    ItemRead,
    ItemUpdate,
    LinkCreate,
)
from app.schemas.people import PersonRead
from app.services import activity, blobs, files, permissions, projects
from app.services.paging import PageRequest
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["files"])


async def resolved_folder(
    folder_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> Folder:
    """Turn the path segment into a folder, 404-ing if nothing matches — or if it is
    on a project the caller is not a member of."""
    folder = await files.get_folder(session, folder_id)
    await projects.require_reachable(
        session, folder.project_id, principal, NotFoundError("No folder with that id.")
    )
    return folder


async def resolved_item(
    item_id: UUID,
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> FileItem:
    """Turn the path segment into a file or link, 404-ing if nothing matches —
    or if it is on a project the caller is not a member of."""
    item = await files.get_item(session, item_id)
    await projects.require_reachable(
        session,
        await files.project_id_of_item(session, item),
        principal,
        NotFoundError("No file or link with that id."),
    )
    return item


def _folder_read(folder: Folder) -> FolderRead:
    return FolderRead(
        id=folder.id,
        project_id=folder.project_id,
        parent_id=folder.parent_id,
        name=folder.name,
        is_root=folder.is_root,
        default_sensitivity=sensitivity.parse(folder.default_sensitivity, DEFAULT_LEVEL),
        created_at=folder.created_at,
    )


def file_item_read(item: FileItem) -> ItemRead:
    """One item as a listing shows it. Public, because a CSV cold storage
    writes is reported in the same shape as anything uploaded."""
    person = item.added_by_person
    return ItemRead(
        id=item.id,
        folder_id=item.folder_id,
        kind=item.kind,
        name=item.name,
        sensitivity=sensitivity.parse(item.sensitivity, DEFAULT_LEVEL),
        url=item.url,
        source=item.source,
        size=item.size,
        mime=item.mime,
        added_by=PersonRead.model_validate(person) if person is not None else None,
        created_at=item.created_at,
    )


def _filed_item(filed: files.Filed) -> FiledItem:
    """An item plus where it is filed, built from the plain row's own shape so
    the two listings cannot drift apart."""
    return FiledItem(**file_item_read(filed.item).model_dump(), folder_path=filed.folder_path)


def _nested_folders(folders: list[Folder]) -> list[FolderNode]:
    """Turn the project's flat folder list into the tree it describes."""
    by_parent: dict[UUID | None, list[Folder]] = defaultdict(list)
    for folder in folders:
        by_parent[folder.parent_id].append(folder)

    def branch(parent_id: UUID | None) -> list[FolderNode]:
        return [
            FolderNode(
                id=folder.id,
                name=folder.name,
                parent_id=folder.parent_id,
                is_root=folder.is_root,
                children=branch(folder.id),
            )
            for folder in by_parent[parent_id]
        ]

    return branch(None)


# --- Folders ---------------------------------------------------------------


async def visible_item(
    item: FileItem = Depends(resolved_item),
    principal: Principal = Depends(current_principal),
    session: AsyncSession = SessionDependency,
) -> FileItem:
    """The item, if this caller is cleared to know it exists — else a 404.

    Every path that reaches one item goes through this: the reads, the
    download that hands over the bytes, and the writes, because a role that
    cannot be shown a file has no business renaming it either.

    It checks no scope of its own. Each endpoint below already says which one
    it wants, and a second `read` check here would refuse a `write`-only token
    on a path its own guard had allowed.
    """
    project_id = await files.project_id_of_item(session, item)
    allowed = await permissions.visible_levels_for(session, project_id, principal)
    permissions.enforce_visible(item.sensitivity, allowed, "file or link")
    return item


WRITE_FILES = guards.on_project(Permission.FILES)
WRITE_IN_THIS_FOLDER = guards.for_entity(Permission.FILES, resolved_folder)
WRITE_THIS_ITEM = guards.for_entity(Permission.FILES, visible_item, locate=files.project_id_of_item)


@router.get(
    "/projects/{project_ref}/folders",
    response_model=FolderPage,
    summary="List a project's folders",
)
async def list_folders(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> FolderPage:
    """The folders in the project, flat and alphabetical, a page at a time.

    Use `/tree` for the same folders arranged as a tree.
    """
    found = await files.folder_page(session, project, page)
    return FolderPage(items=[_folder_read(folder) for folder in found.rows], next=found.next)


@router.post(
    "/projects/{project_ref}/folders",
    response_model=FolderRead,
    status_code=status.HTTP_201_CREATED,
    summary="Create a folder",
    responses={409: {"description": "The parent already has a folder with that name."}},
)
async def create_folder(
    body: FolderCreate,
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(WRITE_FILES),
    session: AsyncSession = SessionDependency,
) -> FolderRead:
    """Add a folder. Omit `parent_id` to put it in the project's root folder.

    Every project has exactly one root folder, created with it and named after
    it. Nothing sits outside the root, so a folder with no parent named is a
    folder in the root rather than a folder nowhere.
    """
    folder = await files.create_folder(session, project, body)
    await activity.record(
        session,
        principal,
        "folder.created",
        entity_type="folder",
        entity_id=folder.id,
        project_id=project.id,
        payload={
            "name": folder.name,
            "parent_id": str(folder.parent_id) if folder.parent_id else None,
        },
    )
    return _folder_read(folder)


@router.get(
    "/projects/{project_ref}/tree",
    response_model=FolderNode,
    summary="Get the whole folder tree",
)
async def get_tree(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
) -> FolderNode:
    """The project's root folder, with every folder beneath it nested inside.

    One node rather than a list, because a project has exactly one root and
    saying so in the response type spares every client the question of what a
    second entry would have meant.

    One call for the entire tree: the files screen needs all of it to draw its
    left-hand pane, and a request per expanded node would make every click wait
    on the network.
    """
    tree = _nested_folders(await files.list_folders(session, project))
    if not tree:
        raise UnprocessableRequestError(
            "This project has no root folder.", details={"project_id": str(project.id)}
        )
    return tree[0]


@router.get(
    "/folders/{folder_id}/children",
    response_model=FolderChildren,
    summary="List what a folder holds",
)
async def get_children(
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> FolderChildren:
    """The subfolders and a page of the items directly inside a folder, plus
    its breadcrumb.

    The items are paged — `after` and `limit` — and the subfolders are not:
    they are the tree's structure, which `/tree` already returns whole.

    Items classified above the caller's clearance are left out rather than
    listed and refused — see :func:`app.services.permissions.enforce_visible`.
    Subfolders are structure and are always listed: a tree with holes in it is
    harder to trust than one with locked drawers.
    """
    allowed = await permissions.visible_levels_for(session, folder.project_id, principal)
    items = await files.items_in(session, folder, page, allowed=allowed)
    path = await files.path_to(session, folder)
    return FolderChildren(
        folder=_folder_read(folder),
        path=[FolderCrumb(id=crumb.id, name=crumb.name) for crumb in path],
        folders=[_folder_read(subfolder) for subfolder in await files.subfolders(session, folder)],
        items=[file_item_read(item) for item in items.rows],
        next=items.next,
    )


@router.patch(
    "/folders/{folder_id}",
    response_model=FolderRead,
    summary="Rename or move a folder",
    responses={
        409: {"description": "The destination already has a folder with that name."},
        422: {
            "description": (
                "The folder is the project's root, or the move would put it inside its own subtree."
            )
        },
    },
)
async def update_folder(
    body: FolderUpdate,
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(WRITE_IN_THIS_FOLDER),
    session: AsyncSession = SessionDependency,
) -> FolderRead:
    """Change a folder's name, its parent, or both.

    Send `parent_id: null` to move it into the project's root; omit `parent_id`
    entirely to leave it where it is. The root folder itself is refused with a
    422: it is named after the project and goes when the project goes.
    """
    updated = await files.update_folder(session, folder, body)
    await activity.record(
        session,
        principal,
        "folder.updated",
        entity_type="folder",
        entity_id=updated.id,
        project_id=updated.project_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True)), "name": updated.name},
    )
    return _folder_read(updated)


@router.delete(
    "/folders/{folder_id}",
    response_model=Acknowledged,
    summary="Delete a folder",
    responses={422: {"description": "The folder is the project's root."}},
)
async def delete_folder(
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(WRITE_IN_THIS_FOLDER),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Delete a folder and everything inside it, subfolders included.

    Uploaded content is deleted from disk too, but only where no file outside
    this folder shares it. The project's root folder cannot be deleted; archive
    the project instead.
    """
    name, project_id = folder.name, folder.project_id
    await files.delete_folder(session, folder)
    await activity.record(
        session,
        principal,
        "folder.deleted",
        entity_type="folder",
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()


# --- Items -----------------------------------------------------------------


@router.post(
    "/folders/{folder_id}/upload",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a file",
    responses={
        409: {"description": "The folder already has something with that name."},
        413: {"description": "The file is larger than the configured upload limit."},
    },
    openapi_extra=multipart_body(
        {
            "file": {
                "type": "string",
                "contentMediaType": "application/octet-stream",
                "description": "The file itself.",
            },
            "added_by": {
                "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}],
                "description": "Which person is uploading it.",
            },
            "sensitivity": {
                "anyOf": [{"$ref": "#/components/schemas/Sensitivity"}, {"type": "null"}],
                "description": "How far it may travel. Omit to take the folder's default.",
            },
        },
        required=["file"],
    ),
)
@streams_upload
async def upload_file(
    request: Request,
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(WRITE_IN_THIS_FOLDER),
    session: AsyncSession = SessionDependency,
    store: BlobStore = Depends(get_blob_store),
    settings: Settings = Depends(app_settings),
) -> ItemRead:
    """Store a file in this folder as `multipart/form-data`.

    Content is addressed by its SHA-256, so uploading bytes the server already
    holds costs a row and no disk. Anything over `CYLIST_MAX_UPLOAD_MB` is
    refused with a 413 — before any of it is read, if the request says how
    long it is, and part-way through if it does not.

    The body is read only once the caller is known to be allowed to upload
    here (CYLIST-123). Send `added_by` and `sensitivity` before `file` and
    they are checked before the file is stored, too.
    """
    item = await files.upload(
        session, store, folder, multipart.Form(request), max_bytes=settings.max_upload_bytes
    )
    await activity.record(
        session,
        principal,
        "file.uploaded",
        entity_type="item",
        entity_id=item.id,
        project_id=folder.project_id,
        payload={"name": item.name, "size": item.size, "sensitivity": item.sensitivity},
    )
    return file_item_read(item)


@router.post(
    "/folders/{folder_id}/links",
    response_model=ItemRead,
    status_code=status.HTTP_201_CREATED,
    summary="Add a link",
    responses={409: {"description": "The folder already has something with that name."}},
)
async def add_link(
    body: LinkCreate,
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(WRITE_IN_THIS_FOLDER),
    session: AsyncSession = SessionDependency,
) -> ItemRead:
    """Put a link to a SharePoint or Drive document in this folder.

    Links sit in the same folders as uploads because that is where you look for
    them — the distinction between "ours" and "theirs" matters far less than
    the subject the document is about.
    """
    item = await files.add_link(session, folder, body)
    await activity.record(
        session,
        principal,
        "link.added",
        entity_type="item",
        entity_id=item.id,
        project_id=folder.project_id,
        payload={"name": item.name, "source": item.source.value},
    )
    return file_item_read(item)


@router.get(
    "/projects/{project_ref}/items",
    response_model=FiledItemPage,
    summary="List every file and link in a project",
)
async def list_project_items(
    project: Project = Depends(resolved_project),
    principal: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = SessionDependency,
    page: PageRequest = PageQuery,
) -> FiledItemPage:
    """The items in the project, flat and alphabetical, each with its path, a
    page at a time.

    The tree without the tree, for the places that want a project's files as a
    list of names: the `>` tag in a description or a comment is completed from
    this, and so is anything else that has to recognise a file by name rather
    than find it by walking. Use `/folders/{folder_id}/children` to browse.
    """
    allowed = await permissions.readable_levels(session, project, principal)
    found, after = await files.list_items(session, project, page, allowed=allowed)
    return FiledItemPage(items=[_filed_item(filed) for filed in found], next=after)


@router.get("/items/{item_id}", response_model=ItemRead, summary="Get a file or link")
async def get_item(
    item: FileItem = Depends(visible_item),
    _: Principal = Depends(require(Scope.READ)),
) -> ItemRead:
    return file_item_read(item)


@router.patch(
    "/items/{item_id}",
    response_model=ItemRead,
    summary="Update a file or link",
    responses={409: {"description": "The folder already has something with that name."}},
)
async def update_item(
    body: ItemUpdate,
    item: FileItem = Depends(resolved_item),
    principal: Principal = Depends(WRITE_THIS_ITEM),
    session: AsyncSession = SessionDependency,
) -> ItemRead:
    """Rename an item, correct a link's target, or change who added it."""
    updated = await files.update_item(session, item, body)
    await activity.record(
        session,
        principal,
        "item.updated",
        entity_type="item",
        entity_id=updated.id,
        project_id=await files.project_id_of_item(session, updated),
        payload={"fields": sorted(body.model_dump(exclude_unset=True)), "name": updated.name},
    )
    return file_item_read(updated)


@router.delete("/items/{item_id}", response_model=Acknowledged, summary="Delete a file or link")
async def delete_item(
    item: FileItem = Depends(resolved_item),
    principal: Principal = Depends(WRITE_THIS_ITEM),
    session: AsyncSession = SessionDependency,
) -> Acknowledged:
    """Remove a file or link.

    A file's content leaves the disk with it, unless another file was uploaded
    from the same bytes and still needs it.
    """
    name = item.name
    project_id = await files.project_id_of_item(session, item)
    await files.delete_item(session, item)
    await activity.record(
        session,
        principal,
        "item.deleted",
        entity_type="item",
        project_id=project_id,
        payload={"name": name},
    )
    return Acknowledged()


@router.get(
    "/items/{item_id}/download",
    response_class=FileResponse,
    summary="Download a file",
    responses={
        200: {"content": {"application/octet-stream": {}}, "description": "The file's content."},
        422: {"description": "The item is a link; open its URL instead."},
    },
)
async def download_item(
    item: FileItem = Depends(visible_item),
    _: Principal = Depends(require(Scope.READ)),
    store: BlobStore = Depends(get_blob_store),
) -> FileResponse:
    """Send a file's content back, named as it was uploaded.

    Links have nothing to send: their content is on someone else's server, so
    the client opens `url` instead.
    """
    if item.kind is not ItemKind.FILE or item.blob is None:
        raise UnprocessableRequestError(
            "That item is a link. Open its URL instead of downloading it.",
            details={"url": item.url},
        )

    if not await store.exists(item.blob.path):
        raise NotFoundError(
            "This file's content is missing from the store.", details={"name": item.name}
        )

    return FileResponse(
        store.locate(item.blob.path),
        media_type=blobs.declared_mime(item.mime),
        filename=item.name,
        headers=DOWNLOAD_HEADERS,
    )
