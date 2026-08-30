"""A project's files: folders, the items in them, and the bytes behind them.

Three tables rather than one, because they answer different questions:

* ``folder`` is the shape of the tree,
* ``file_item`` is what a listing shows — an upload *or* a link out to
  SharePoint or Drive, which sit side by side in the same folders,
* ``blob`` is the content, addressed by its SHA-256 so the same bytes uploaded
  twice cost one copy on disk.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Enum,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import Person

NAME_MAX_LENGTH = 255
SHA256_LENGTH = 64


class ItemKind(StrEnum):
    """Whether an item's content is here or somewhere else."""

    FILE = "file"
    """Bytes we hold, in a :class:`Blob`."""

    LINK = "link"
    """A URL to a document living in someone else's system."""


class ItemSource(StrEnum):
    """Where an item came from — the listing's Source column."""

    UPLOAD = "upload"
    SHAREPOINT = "sharepoint"
    GDRIVE = "gdrive"
    OTHER = "other"


class Blob(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """The bytes of an uploaded file, held once per distinct content."""

    __tablename__ = "blob"
    __table_args__ = (Index("ix_blob_sha256", "sha256", unique=True),)

    sha256: Mapped[str] = mapped_column(String(SHA256_LENGTH), nullable=False)
    """Lowercase hex digest of the content. The unique index on it is what
    makes a second upload of the same bytes reuse this row."""

    size: Mapped[int] = mapped_column(BigInteger, nullable=False)

    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    """The type the first upload of these bytes declared. The content is what
    is shared, not the label, so each ``file_item`` keeps its own copy of the
    type it was sent with."""

    path: Mapped[str] = mapped_column(Text, nullable=False)
    """Location relative to ``DATA_DIR/blobs`` — ``ab/cd/<sha256>``. Recorded
    rather than derived so the layout on disk can change without every reader
    having to agree on the new rule."""


class Folder(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A node in a project's file tree.

    An adjacency list, with one row per project reserved: ``parent_id IS NULL``
    is the project's **root**, and everything else hangs beneath it. The root
    is a real row rather than an implicit "no parent" so that a file can sit at
    the top of a project — a README belongs beside the tree, not inside a
    folder somebody had to invent for it.

    "Exactly one root" is a fact of the schema, not a convention the service
    remembers: :data:`ix_folder_project_id_root` is unique on ``project_id``
    over precisely the parentless rows. An ``is_root`` flag would have said the
    same thing twice and left room for the two answers to disagree.
    """

    __tablename__ = "folder"
    __table_args__ = (
        # One parentless folder per project. This subsumes the uniqueness of
        # top-level names, because there is now only one top-level folder.
        Index(
            "ix_folder_project_id_root",
            "project_id",
            unique=True,
            postgresql_where=text("parent_id IS NULL"),
        ),
        # Partial, because Postgres treats NULLs as distinct: a plain unique
        # index on (parent_id, name) would not constrain the roots at all.
        Index(
            "ix_folder_parent_id_name",
            "parent_id",
            "name",
            unique=True,
            postgresql_where=text("parent_id IS NOT NULL"),
        ),
        Index("ix_folder_project_id_parent_id", "project_id", "parent_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    parent_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("folder.id", ondelete="CASCADE"),
    )
    """Deleting a folder takes its subtree with it, in the database rather than
    in Python, so an interrupted delete cannot strand a branch."""

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    """What the tree shows. A root's name is its project's, kept in step by
    :mod:`app.services.projects` — the root *is* the project, so a listing that
    called it anything else would be describing something that does not
    exist."""

    @property
    def is_root(self) -> bool:
        """Whether this is the project's root, which cannot move or go."""
        return self.parent_id is None


class FileItem(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One row of a folder listing: an uploaded file, or a link."""

    __tablename__ = "file_item"
    __table_args__ = (
        # The two kinds are mutually exclusive in the database, not merely in
        # the service: a "file" with no bytes cannot be downloaded, and a
        # "link" with bytes is two answers to the question of what it holds.
        CheckConstraint(
            "(kind = 'file' AND blob_id IS NOT NULL AND url IS NULL)"
            " OR (kind = 'link' AND url IS NOT NULL AND blob_id IS NULL)",
            name="kind_matches_content",
        ),
        Index("ix_file_item_folder_id_name", "folder_id", "name", unique=True),
        Index("ix_file_item_blob_id", "blob_id"),
    )

    folder_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("folder.id", ondelete="CASCADE"),
        nullable=False,
    )

    kind: Mapped[ItemKind] = mapped_column(
        Enum(
            ItemKind,
            name="item_kind",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)

    blob_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("blob.id", ondelete="RESTRICT"),
    )
    """RESTRICT rather than CASCADE: a blob is shared, so it may only go once
    nothing references it. :mod:`app.services.files` is what decides that."""

    url: Mapped[str | None] = mapped_column(Text)

    source: Mapped[ItemSource] = mapped_column(
        Enum(
            ItemSource,
            name="item_source",
            native_enum=False,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    size: Mapped[int | None] = mapped_column(BigInteger)
    """Bytes, copied from the blob so a listing needs no join. Null for links,
    whose size we cannot know without fetching them."""

    mime: Mapped[str | None] = mapped_column(String(255))

    added_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Who put it here. Nullable because an API token is not a person, and
    because the directory is a directory of people, not of accounts."""

    added_by_person: Mapped[Person | None] = relationship(lazy="selectin")
    """The row behind :attr:`added_by`. Eagerly loaded: every listing draws the
    uploader's avatar, and fetching them one at a time would be N+1."""

    blob: Mapped[Blob | None] = relationship(lazy="joined")
