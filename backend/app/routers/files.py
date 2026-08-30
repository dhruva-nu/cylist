"""A project's files: folders, uploads, links and downloads.

One router rather than three despite the three prefixes. Folders hang off a
project, but items and downloads are addressed by their own id — a file keeps
its URL when it is moved — so splitting by prefix would scatter one feature
across three modules for no gain.
"""

from __future__ import annotations

from collections import defaultdict
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.dependencies import require
from app.auth.principal import Principal
from app.auth.scopes import Scope
from app.config import Settings, app_settings
from app.core.errors import NotFoundError, UnprocessableRequestError
from app.db import get_session
from app.models.file import FileItem, Folder, ItemKind
from app.models.project import Project
from app.routers.projects import resolved_project
from app.schemas.common import Acknowledged
from app.schemas.files import (
    FolderChildren,
    FolderCreate,
    FolderCrumb,
    FolderNode,
    FolderRead,
    FolderUpdate,
    ItemRead,
    ItemUpdate,
    LinkCreate,
)
from app.schemas.people import PersonRead
from app.services import activity, files
from app.storage import BlobStore, get_blob_store

router = APIRouter(tags=["files"])


async def resolved_folder(
    folder_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> Folder:
    """Turn the path segment into a folder, 404-ing if nothing matches."""
    return await files.get_folder(session, folder_id)


async def resolved_item(
    item_id: UUID,
    session: AsyncSession = Depends(get_session),
) -> FileItem:
    """Turn the path segment into a file or link, 404-ing if nothing matches."""
    return await files.get_item(session, item_id)


def _folder(folder: Folder) -> FolderRead:
    return FolderRead(
        id=folder.id,
        project_id=folder.project_id,
        parent_id=folder.parent_id,
        name=folder.name,
        is_root=folder.is_root,
        created_at=folder.created_at,
    )


def _item(item: FileItem) -> ItemRead:
    person = item.added_by_person
    return ItemRead(
        id=item.id,
        folder_id=item.folder_id,
        kind=item.kind,
        name=item.name,
        url=item.url,
        source=item.source,
        size=item.size,
        mime=item.mime,
        added_by=PersonRead.model_validate(person) if person is not None else None,
        created_at=item.created_at,
    )


def _nest(folders: list[Folder]) -> list[FolderNode]:
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


@router.get(
    "/projects/{project_ref}/folders",
    response_model=list[FolderRead],
    summary="List a project's folders",
)
async def list_folders(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
) -> list[FolderRead]:
    """Every folder in the project, flat and alphabetical.

    Use `/tree` for the same folders arranged as a tree.
    """
    return [_folder(folder) for folder in await files.list_folders(session, project)]


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
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
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
    return _folder(folder)


@router.get(
    "/projects/{project_ref}/tree",
    response_model=FolderNode,
    summary="Get the whole folder tree",
)
async def get_tree(
    project: Project = Depends(resolved_project),
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
) -> FolderNode:
    """The project's root folder, with every folder beneath it nested inside.

    One node rather than a list, because a project has exactly one root and
    saying so in the response type spares every client the question of what a
    second entry would have meant.

    One call for the entire tree: the files screen needs all of it to draw its
    left-hand pane, and a request per expanded node would make every click wait
    on the network.
    """
    tree = _nest(await files.list_folders(session, project))
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
    _: Principal = Depends(require(Scope.READ)),
    session: AsyncSession = Depends(get_session),
) -> FolderChildren:
    """The subfolders and items directly inside a folder, plus its breadcrumb."""
    subfolders, items = await files.children(session, folder)
    path = await files.path_to(session, folder)
    return FolderChildren(
        folder=_folder(folder),
        path=[FolderCrumb(id=crumb.id, name=crumb.name) for crumb in path],
        folders=[_folder(subfolder) for subfolder in subfolders],
        items=[_item(item) for item in items],
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
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
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
    return _folder(updated)


@router.delete(
    "/folders/{folder_id}",
    response_model=Acknowledged,
    summary="Delete a folder",
    responses={422: {"description": "The folder is the project's root."}},
)
async def delete_folder(
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_blob_store),
) -> Acknowledged:
    """Delete a folder and everything inside it, subfolders included.

    Uploaded content is deleted from disk too, but only where no file outside
    this folder shares it. The project's root folder cannot be deleted; archive
    the project instead.
    """
    name, project_id = folder.name, folder.project_id
    await files.delete_folder(session, store, folder)
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
)
async def upload_file(
    folder: Folder = Depends(resolved_folder),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_blob_store),
    settings: Settings = Depends(app_settings),
    file: UploadFile = File(description="The file itself."),
    added_by: UUID | None = Form(default=None, description="Which person is uploading it."),
) -> ItemRead:
    """Store a file in this folder as `multipart/form-data`.

    Content is addressed by its SHA-256, so uploading bytes the server already
    holds costs a row and no disk. Anything over `CYLIST_MAX_UPLOAD_MB` is
    refused with a 413 part-way through rather than after it has all arrived.
    """
    item = await files.upload(
        session,
        store,
        folder,
        file,
        max_bytes=settings.max_upload_bytes,
        added_by=added_by,
    )
    await activity.record(
        session,
        principal,
        "file.uploaded",
        entity_type="item",
        entity_id=item.id,
        project_id=folder.project_id,
        payload={"name": item.name, "size": item.size},
    )
    return _item(item)


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
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
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
    return _item(item)


@router.get("/items/{item_id}", response_model=ItemRead, summary="Get a file or link")
async def get_item(
    item: FileItem = Depends(resolved_item),
    _: Principal = Depends(require(Scope.READ)),
) -> ItemRead:
    return _item(item)


@router.patch(
    "/items/{item_id}",
    response_model=ItemRead,
    summary="Update a file or link",
    responses={409: {"description": "The folder already has something with that name."}},
)
async def update_item(
    body: ItemUpdate,
    item: FileItem = Depends(resolved_item),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
) -> ItemRead:
    """Rename an item, correct a link's target, or change who added it."""
    updated = await files.update_item(session, item, body)
    folder = await files.get_folder(session, updated.folder_id)
    await activity.record(
        session,
        principal,
        "item.updated",
        entity_type="item",
        entity_id=updated.id,
        project_id=folder.project_id,
        payload={"fields": sorted(body.model_dump(exclude_unset=True)), "name": updated.name},
    )
    return _item(updated)


@router.delete("/items/{item_id}", response_model=Acknowledged, summary="Delete a file or link")
async def delete_item(
    item: FileItem = Depends(resolved_item),
    principal: Principal = Depends(require(Scope.WRITE)),
    session: AsyncSession = Depends(get_session),
    store: BlobStore = Depends(get_blob_store),
) -> Acknowledged:
    """Remove a file or link.

    A file's content leaves the disk with it, unless another file was uploaded
    from the same bytes and still needs it.
    """
    name = item.name
    folder = await files.get_folder(session, item.folder_id)
    await files.delete_item(session, store, item)
    await activity.record(
        session,
        principal,
        "item.deleted",
        entity_type="item",
        project_id=folder.project_id,
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
    item: FileItem = Depends(resolved_item),
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
        media_type=item.mime or files.DEFAULT_MIME,
        filename=item.name,
    )
