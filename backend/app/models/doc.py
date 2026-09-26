"""Docs: a project's markdown, filed by section and topic.

A project's docs are read as a two-level tree: **section → topic → doc**.

* **Sections are fixed.** Every project has the same two, *Product* and
  *Engineering*, and nobody can add a third. They are an enum on the topic
  rather than a table because they are a vocabulary, not content: a reader
  moving between projects finds the same two headings in the same place, and
  an agent told to "file this under Engineering" never has to ask which
  Engineering.
* **Topics are the project's own**, one level deep. "MCP", "DB schema",
  "Goals" — named by the people on the board, ordered by hand within their
  section. No nesting, because a tree that can grow without limit is a tree
  nobody can find anything in.
* **A doc lives in exactly one topic**, and moving it is changing that one
  column. Its markdown is stored inline — a doc is text somebody reads on the
  page, not an upload, so it is not a blob and has no place in the file tree.

Deleting a topic that still holds docs is refused rather than cascaded — see
:func:`app.services.docs.delete_topic`. A topic is a heading; the docs under it
are the work, and a heading is not worth losing them over.
"""

from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from sqlalchemy import (
    Enum,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.person import Person

TOPIC_NAME_MAX_LENGTH = 80
TITLE_MAX_LENGTH = 200


class DocSection(StrEnum):
    """One of the two headings every project's docs are filed under."""

    PRODUCT = "product"
    """What the thing is for and how it behaves — goals, flows, the words the
    board uses."""

    ENGINEERING = "engineering"
    """How it is built — interfaces, schema, the parts an engineer or an agent
    has to know to change it."""

    @property
    def label(self) -> str:
        return _SECTION_LABELS[self]


_SECTION_LABELS = {
    DocSection.PRODUCT: "Product",
    DocSection.ENGINEERING: "Engineering",
}

SECTION_ORDER: tuple[DocSection, ...] = (DocSection.PRODUCT, DocSection.ENGINEERING)
"""The order the tree draws the sections in. Product first: what a thing is for
reads before how it is built."""


class DocTopic(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "doc_topic"
    __table_args__ = (
        # Named once per section, like a goal on its project: a doc is filed
        # by naming its topic, and two "APIs" under Engineering would make
        # that a guess. The same name under both sections is fine — "Tasks"
        # the product idea and "Tasks" the table are different topics.
        Index("ix_doc_topic_project_id_section_name", "project_id", "section", "name", unique=True),
        # What a doc's composite key points at, so a doc can only be filed
        # under a topic on its own project.
        UniqueConstraint("project_id", "id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    section: Mapped[DocSection] = mapped_column(
        Enum(
            DocSection,
            name="doc_section",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(TOPIC_NAME_MAX_LENGTH), nullable=False)

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Order within the section, from the top. Rewritten as 0..n-1 whenever the
    section is reordered, so it is only ever compared, never shown."""


class Doc(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "doc"
    __table_args__ = (
        # Carried with the project, like a permission row with its role, so
        # the database rather than the service is what keeps a doc on its own
        # project's topics. Refusing, not cascading: deleting a topic that
        # still holds docs is a mistake the service names before it gets here.
        ForeignKeyConstraint(
            ["project_id", "topic_id"],
            ["doc_topic.project_id", "doc_topic.id"],
            name="fk_doc_project_id_topic_id_doc_topic",
        ),
        Index("ix_doc_topic_id_position", "topic_id", "position"),
        Index("ix_doc_project_id", "project_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Carried on the row even though the topic knows it, for the composite key
    above and so a project's doc count is one query."""

    topic_id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), nullable=False)

    title: Mapped[str] = mapped_column(String(TITLE_MAX_LENGTH), nullable=False)

    body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """Markdown, as written. Rendered by the reader, never stored as HTML."""

    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    """Order within the topic, from the top. Compacted to 0..n-1 whenever the
    topic's docs are reordered or one moves in or out."""

    author_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Who wrote it first — most often an agent. Null when the credential that
    wrote it was nobody in the directory, or the person has since gone."""

    author: Mapped[Person | None] = relationship(lazy="selectin")
