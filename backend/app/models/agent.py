"""What an agent works from on a project: its skills, and its scratchpad.

Two tables, because they are written by different hands and read at different
times:

* ``skill`` is a file somebody uploads — a packaged job an agent can be handed.
  It reuses :class:`~app.models.file.Blob` for the bytes, so a skill and a file
  with the same content cost one copy on disk.
* ``agent_note`` is the scratchpad: one short line an agent writes when it
  learns something about this project that it would otherwise have to work out
  again next time.

A skill is not filed in the project's folder tree. It could have been — the
tree already holds uploads — but a skill is not a document about the project,
it is a thing an agent is given, and burying it in `Files/` would leave the
question of which folder is the one that counts.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String, Text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin
from app.models.file import Blob
from app.models.person import Person

NAME_MAX_LENGTH = 255

NOTE_MAX_LENGTH = 280
"""The longest a scratchpad line may be.

A cap in the schema rather than a note in the documentation, because "very
minimal words" is the whole point of the scratchpad: a page of prose an agent
appended is a page nobody reads, and the next agent along has to read all of
it before it can start. Enforced in the database so that neither the HTTP API
nor the MCP server is the only thing standing between a model and an essay.
"""


class Skill(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """An uploaded skill: the file, and who put it on the project."""

    __tablename__ = "skill"
    __table_args__ = (
        # One skill of a given name per project. Uploading a new version of a
        # skill replaces it by name, which is what makes the name worth
        # constraining rather than merely indexing.
        Index("ix_skill_project_id_name", "project_id", "name", unique=True),
        Index("ix_skill_blob_id", "blob_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
    """The filename it was uploaded under — ``board-tidy.md``, ``day-report.md``."""

    description: Mapped[str | None] = mapped_column(Text)
    """One line on what the skill does, if the uploader gave one. Optional
    because a skill's own first line usually says, and making somebody restate
    it is how the two come to disagree."""

    blob_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("blob.id", ondelete="RESTRICT"),
        nullable=False,
    )
    """RESTRICT rather than CASCADE, as for a file item: the bytes are shared,
    so they may only go once nothing refers to them."""

    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    """Copied from the blob so a listing needs no join."""

    mime: Mapped[str] = mapped_column(String(255), nullable=False)

    added_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Nullable for the same reason a file's is: an API token is not a person."""

    added_by_person: Mapped[Person | None] = relationship(lazy="selectin")

    blob: Mapped[Blob] = relationship(lazy="joined")


class AgentNote(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One line on the project's scratchpad.

    Append-only in spirit: a note is written when something is learned and
    deleted when it stops being true. There is no edit, because a note that has
    been rewritten is a different thing learned and its own timestamp is part
    of what it says.
    """

    __tablename__ = "agent_note"
    __table_args__ = (
        CheckConstraint(
            f"char_length(btrim(body)) BETWEEN 1 AND {NOTE_MAX_LENGTH}",
            name="body_is_short_and_not_blank",
        ),
        # Newest first, per project — the only way this table is ever read.
        Index("ix_agent_note_project_id_created_at", "project_id", "created_at"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    body: Mapped[str] = mapped_column(String(NOTE_MAX_LENGTH), nullable=False)
    """What was learned, in a line."""

    author_label: Mapped[str] = mapped_column(String(120), nullable=False)
    """The name on the credential that wrote it — a token's label, or the owner
    for a browser session. Not a person id: the thing that learns something is
    the agent, and an agent is a token rather than somebody in the directory.
    Recorded as text so a revoked token leaves its notes legible."""

    added_by: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Set when a person wrote the note themselves, which the scratchpad allows
    — reading it is most of its value, and correcting it by hand should not
    mean pretending to be an agent."""

    added_by_person: Mapped[Person | None] = relationship(lazy="selectin")
