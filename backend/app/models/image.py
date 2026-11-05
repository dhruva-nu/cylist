"""Images pasted into a card's description (CYLIST-182).

A description is markdown, and an image in it is ``![name](/api/v1/images/<id>)``
— a reference to a row here, never the bytes themselves. The bytes go to the
blob store like any upload, so a screenshot pasted into two cards costs one
copy on disk and the ``task`` table stays text.

An image belongs to the *project* rather than to a card, because it is
uploaded while a description is being written — which, for a new card, is
before the card exists. It lives as long as the project does.

Not filed in the project's folder tree, for the reason a skill is not: a
screenshot in a bug report is not a document about the project, and a
``Files/`` that filled up with them would bury the ones that are.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, String
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.file import Blob


class DescriptionImage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One uploaded image, addressable by id from any description on its project."""

    __tablename__ = "description_image"
    __table_args__ = (
        Index("ix_description_image_project_id", "project_id"),
        Index("ix_description_image_blob_id", "blob_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Whose members may see it. Serving one checks this, not which card's
    description happens to mention it."""

    blob_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("blob.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """RESTRICT, as for a file item or a skill: the bytes are shared."""

    mime: Mapped[str] = mapped_column(String(255), nullable=False)
    """The type read off the bytes on upload, not the one the client declared
    — it is sent back inline, so it has to be true."""

    added_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Who uploaded it. Nullable because an API token is not a person."""

    blob: Mapped[Blob] = relationship(lazy="joined")
    """Joined into every load: serving an image needs its path."""

    @property
    def size(self) -> int:
        """Bytes, read off the blob."""
        return self.blob.size
