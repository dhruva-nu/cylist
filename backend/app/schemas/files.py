"""Folders, uploaded files and links."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from app.models.file import NAME_MAX_LENGTH, ItemKind, ItemSource
from app.schemas.common import Schema
from app.schemas.people import PersonRead

_URL_PATTERN = r"^https?://\S+$"


def clean_name(value: str) -> str:
    """Reject a name that is blank or that is trying to be a path.

    Names are shown in a tree and sent back in a download's filename, never
    used to address anything on disk — but a folder called ``../secrets`` would
    still be a lie about where its contents are.
    """
    stripped = value.strip()
    if not stripped:
        raise ValueError("must not be blank")
    if stripped in {".", ".."} or "/" in stripped or "\\" in stripped:
        raise ValueError("must not contain a path")
    if any(character < " " or character == "\x7f" for character in stripped):
        raise ValueError("must not contain control characters")
    return stripped


class FolderCreate(Schema):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    parent_id: UUID | None = Field(
        default=None, description="Omit to put the folder in the project's root folder."
    )

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean_name(value)


class FolderUpdate(Schema):
    """Rename a folder, move it, or both.

    ``parent_id`` distinguishes omitted from null: leaving it out keeps the
    folder where it is, sending `null` moves it to the project's root.

    The root itself accepts neither — it stands for the project, and the API
    says so with a 422 rather than silently ignoring the request.
    """

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    parent_id: UUID | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_name(value) if value is not None else None


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
    folders: list[FolderRead]
    items: list[ItemRead]


class LinkCreate(Schema):
    """A document that lives in SharePoint, Drive or anywhere else."""

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    url: str = Field(pattern=_URL_PATTERN, max_length=2000)
    source: ItemSource = Field(
        default=ItemSource.OTHER, description="Where the document lives. Never `upload`."
    )
    added_by: UUID | None = Field(default=None, description="Which person added it.")

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean_name(value)


class ItemUpdate(Schema):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    url: str | None = Field(default=None, pattern=_URL_PATTERN, max_length=2000)
    source: ItemSource | None = None
    added_by: UUID | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_name(value) if value is not None else None
