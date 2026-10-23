"""The commands the files services take.

Plain inputs to the services, validated as they are built. The HTTP layer
re-exports them (``app.schemas.files``) as the bodies its routes accept,
so there is one definition and the services import nothing from it.
"""

from __future__ import annotations

from uuid import UUID

from pydantic import Field, field_validator

from app.core.sensitivity import Sensitivity
from app.models.file import NAME_MAX_LENGTH, ItemSource
from app.services.commands.base import Command

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
    # Checked here as well as by each schema's max_length, because an upload's
    # filename never passes through a schema: it arrives on the multipart part.
    if len(stripped) > NAME_MAX_LENGTH:
        raise ValueError(f"must be at most {NAME_MAX_LENGTH} characters")
    return stripped


class FolderCreate(Command):
    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    parent_id: UUID | None = Field(
        default=None, description="Omit to put the folder in the project's root folder."
    )
    default_sensitivity: Sensitivity | None = Field(
        default=None,
        description=(
            "What things put in this folder are classified as unless the caller "
            "says. Omit on create to inherit the parent folder's."
        ),
    )

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean_name(value)


class FolderUpdate(Command):
    """Rename a folder, move it, or both.

    ``parent_id`` distinguishes omitted from null: leaving it out keeps the
    folder where it is, sending `null` moves it to the project's root.

    The root itself accepts neither — it stands for the project, and the API
    says so with a 422 rather than silently ignoring the request.
    """

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    parent_id: UUID | None = None
    default_sensitivity: Sensitivity | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_name(value) if value is not None else None


class LinkCreate(Command):
    """A document that lives in SharePoint, Drive or anywhere else."""

    name: str = Field(min_length=1, max_length=NAME_MAX_LENGTH)
    url: str = Field(pattern=_URL_PATTERN, max_length=2000)
    source: ItemSource = Field(
        default=ItemSource.OTHER, description="Where the document lives. Never `upload`."
    )
    added_by: UUID | None = Field(default=None, description="Which person added it.")
    sensitivity: Sensitivity | None = Field(
        default=None,
        description=(
            "How far this may travel. Omit to take the folder's default, which "
            "is how a folder of contracts stays restricted without anybody "
            "remembering to say so on each upload."
        ),
    )

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str) -> str:
        return clean_name(value)


class ItemUpdate(Command):
    """Every field optional; omitted fields are left as they are."""

    name: str | None = Field(default=None, min_length=1, max_length=NAME_MAX_LENGTH)
    sensitivity: Sensitivity | None = Field(
        default=None, description="Reclassify it. Needs the `files` permission like any edit."
    )
    url: str | None = Field(default=None, pattern=_URL_PATTERN, max_length=2000)
    source: ItemSource | None = None
    added_by: UUID | None = None

    @field_validator("name")
    @classmethod
    def _clean(cls, value: str | None) -> str | None:
        return clean_name(value) if value is not None else None
