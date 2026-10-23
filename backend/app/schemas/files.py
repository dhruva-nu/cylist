"""Folders, uploaded files and links."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from app.core.sensitivity import Sensitivity
from app.models.file import ItemKind, ItemSource
from app.schemas.common import Page, Schema
from app.schemas.people import PersonRead
from app.services.commands.files import (
    _URL_PATTERN as _URL_PATTERN,
)
from app.services.commands.files import (
    FolderCreate as FolderCreate,
)
from app.services.commands.files import (
    FolderUpdate as FolderUpdate,
)
from app.services.commands.files import (
    ItemUpdate as ItemUpdate,
)
from app.services.commands.files import (
    LinkCreate as LinkCreate,
)
from app.services.commands.files import (
    clean_name as clean_name,
)


class FolderRead(Schema):
    id: UUID
    project_id: UUID
    parent_id: UUID | None = Field(description="Null only for the project's root folder.")
    name: str
    is_root: bool = Field(
        description=(
            "Whether this is the project's root folder, which is named after the"
            " project and cannot be renamed, moved or deleted."
        )
    )
    default_sensitivity: Sensitivity = Field(
        description="What things uploaded here are classified as unless the caller says."
    )
    created_at: datetime


class FolderNode(Schema):
    """One folder in the whole-tree response, with its subfolders inside it."""

    id: UUID
    name: str
    parent_id: UUID | None
    is_root: bool
    children: list[FolderNode]


# A model that refers to itself cannot finish building while its own name is
# still unbound; this completes it, and fails loudly here rather than on the
# first request if it ever stops resolving.
FolderNode.model_rebuild()


class FolderCrumb(Schema):
    id: UUID
    name: str


class ItemRead(Schema):
    """A row of a folder listing.

    ``added_by`` is the person rather than their id: every listing draws their
    avatar and name, so returning an id would make a second request per row
    inevitable. The endpoints that *set* it take an id.
    """

    id: UUID
    folder_id: UUID
    kind: ItemKind
    name: str
    sensitivity: Sensitivity = Field(
        description="How far this may travel. A role not cleared this high is not shown it."
    )
    url: str | None
    source: ItemSource
    size: int | None
    mime: str | None
    added_by: PersonRead | None
    created_at: datetime


class FiledItem(ItemRead):
    """A row of the project-wide listing: an item, and where it is filed.

    The path is for telling two files of the same name apart. It is a string
    rather than a list of crumbs because nothing navigates with it — a picker
    shows it beside the name, and the name is what a tag carries.
    """

    folder_path: str = Field(
        description=(
            "The folders between the project's root and this item, outermost"
            " first, joined by `/`. Empty for an item at the top of the project."
        )
    )


class FolderChildren(Schema):
    """What one folder holds — the right-hand pane of the files screen."""

    folder: FolderRead
    path: list[FolderCrumb] = Field(
        description=(
            "The folders above this one, outermost first, ending with it."
            " Always starts at the project's root."
        )
    )
    folders: list[FolderRead] = Field(
        description="Every folder directly inside this one. Whole rather than paged: folders "
        "are the tree's structure, and `GET /projects/{ref}/tree` hands that out entire."
    )
    items: list[ItemRead] = Field(
        description="A page of the files and links directly inside this one, alphabetical."
    )
    next: UUID | None = Field(
        description="Pass as `after` for the page of items after these. Null on the last page."
    )


class FolderPage(Page[FolderRead]):
    """A page of a project's folders, alphabetical."""


class FiledItemPage(Page[FiledItem]):
    """A page of a project's files and links, alphabetical, each with its path."""
