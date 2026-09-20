"""Roles: what somebody is on a project.

A role is a name a project's admin invents — "Reviewer", "QA", "Designer" —
and hands to the people on that project. It is per-project rather than global
for the reason membership is: the same person is the one who signs work off on
one board and the one doing the work on another, and a directory-wide role
could only say one of those.

What a role is *allowed* to do is deliberately not here. Cylist's projects are
a shared workspace and this change does not make them otherwise; a role says
who somebody is on a board, the way ``kind`` says which side of the work they
are on. The single exception is the role named below — :attr:`ProjectRole.is_admin`
— which is the one whose holder may create the others, because roles that
anybody could mint would not be roles.

Three things are schema facts rather than conventions:

* **A project has at most one admin role**, by a partial unique index. Two
  would make "who may create a role" a question with two answers.
* **A role is named once per project**, case-folded, for the reason a goal is:
  it is read off a person by name and by colour, and two "Reviewer"s make both
  a guess.
* **A member cannot wear another project's role.** That is the composite
  foreign key on ``project_member`` — see :class:`ProjectRole.__table_args__`
  and the unique constraint that exists only to support it.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

NAME_MAX_LENGTH = 40
"""Short, because a role is worn as a badge beside a name rather than read as a
sentence. "Reviewer" fits; "Reviewer for anything touching billing" is a
description, and there is a column for that."""

ADMIN_ROLE_NAME = "Admin"
"""What the seeded role is called. Fixed: it cannot be renamed, because every
screen that explains who may do what names it in prose."""


class ProjectRole(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "project_role"
    __table_args__ = (
        # Case-folded, because somebody who typed "reviewer" does not think
        # they have made a second role.
        Index(
            "ix_project_role_project_id_name",
            "project_id",
            sql_text("lower(name)"),
            unique=True,
        ),
        Index(
            "ix_project_role_project_id_admin",
            "project_id",
            unique=True,
            postgresql_where=sql_text("is_admin"),
        ),
        # Not a constraint anybody queries. It exists so that `project_member`
        # can point at (project_id, id) rather than at id alone, which is what
        # makes "this member's role belongs to this member's project" true in
        # the database instead of true by inspection.
        UniqueConstraint("project_id", "id", name="uq_project_role_project_id_id"),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)

    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    """What the role means on this board, in the admin's own words. Optional:
    "Reviewer" explains itself, and the ones that do not are exactly the ones
    somebody will write a line about."""

    colour: Mapped[str] = mapped_column(String(7), nullable=False)
    """Six-digit hex, from :data:`app.core.palette.PALETTE` by default, so a
    badge sits in the same family as the avatar beside it."""

    is_admin: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    """Whether holders of this role may manage the project's roles.

    The one role with any authority attached, and the only reason this column
    exists rather than the admin role being recognised by its name — a name is
    a thing people rename, and this one answers a question about access.
    """

    @property
    def is_system(self) -> bool:
        """Whether Cylist made this role rather than a person.

        The same thing as :attr:`is_admin` today, and read separately anyway:
        the rules that hang off it are "cannot be renamed" and "cannot be
        deleted", which are facts about where a role came from rather than
        about what it grants.
        """
        return self.is_admin
