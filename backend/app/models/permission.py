"""What each of a project's roles is allowed to do.

Three tables, and they are **two layers with opposite polarity**, which is the
one thing to hold on to while reading them:

* :class:`ProjectPermission` is a **grant**. No row means no.
* :class:`RoleColumnRule` and :class:`RoleClearance` are **restrictions**. No
  row means no restriction.

That is not an inconsistency, it is the layering. The flat permission answers
"may this role touch cards at all"; the per-object rules only ever narrow what
it already allowed. It also survives a board changing shape: a column added
next month is open to everybody who may move cards, where a grant-shaped table
would have locked every role out of it until an admin noticed.

One row per role per permission, rather than an array on ``project_role``,
because the interesting reads are "what may this role do" and "who may reveal
secrets on this board", and both are a join in this shape and a scan in the
other.

The row with **no role** is the point of the nullable ``role_id``. Somebody on
a project with no role is the state CYLIST-45 made honest — "not said yet" —
and everybody was that state until an admin spoke. They still have to be able
to do something, and what that something is has to be the admin's choice
rather than the server's, so the project carries one set of permissions for
them: :data:`EVERYONE_ELSE`. Putting it in this table rather than in a column
on ``project`` is what lets the screen draw it as one more line of the same
grid, and what lets one endpoint answer "what may each kind of person here
do".

The admin role holds **nothing** in this table and may do everything. Its
authority is :attr:`~app.models.role.ProjectRole.is_admin`, and rows granting
it what it already has would be rows somebody could delete.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import Boolean, ForeignKey, ForeignKeyConstraint, Index, String
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin

EVERYONE_ELSE = None
"""What ``role_id`` is for the project's role-less baseline.

Named so that the reads which care do not have to explain a bare ``None`` —
``permissions[EVERYONE_ELSE]`` says what the row is for where
``permissions[None]`` would only say what it is.
"""


class ProjectPermission(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "project_permission"
    __table_args__ = (
        # A role holds a permission once. Two partial indexes rather than one
        # unique constraint over all three columns, because Postgres counts
        # NULLs as distinct: the baseline rows would be free to duplicate
        # under a plain constraint, and the baseline is exactly the set a
        # double-click on a checkbox would double up.
        Index(
            "ix_project_permission_role_id_permission",
            "role_id",
            "permission",
            unique=True,
            postgresql_where=sql_text("role_id IS NOT NULL"),
        ),
        Index(
            "ix_project_permission_project_id_permission",
            "project_id",
            "permission",
            unique=True,
            postgresql_where=sql_text("role_id IS NULL"),
        ),
        # The same composite, deferred, NO ACTION key `project_member` carries,
        # for the same two reasons — see the long note there. A grant must
        # belong to a role on its own project, and a project being deleted must
        # not depend on which of two cascades Postgres fires first.
        #
        # It is satisfied trivially when `role_id` is NULL, which is what a
        # baseline row wants: MATCH SIMPLE holds a composite key met the moment
        # any of its columns is null.
        ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_project_permission_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Carried on the row even for a role-bearing grant, and not only to make
    the composite key above possible: reading a whole project's grid is one
    query on this column, where going through ``project_role`` would be a join
    that still could not see the baseline."""

    role_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Which role this grant is for, or :data:`EVERYONE_ELSE`."""

    permission: Mapped[str] = mapped_column(String(32), nullable=False)
    """A :class:`~app.auth.permissions.Permission` value.

    Stored as text rather than as a database enum, so that adding one is a
    release rather than a migration and removing one leaves a row that
    :func:`~app.auth.permissions.parse_permissions` can drop on read.
    """


class RoleColumnRule(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """What a role may do at one column of one board.

    A **restriction**: a role with no row for a column may do both things
    there. The service only writes a row that takes something away, and
    deletes one that has stopped doing so — so the table reads as the list of
    exceptions somebody deliberately made, which is also the list worth
    reviewing when a board stops behaving.

    Both columns are false-able independently because they are different
    jobs. Moving a card into Review is work; deciding that a Hotfix in Review
    passes through "Drafted, Reviewed, Merged" is designing the workflow, and
    a team often wants the second in one person's hands while everybody does
    the first.
    """

    __tablename__ = "role_column_rule"
    __table_args__ = (
        Index(
            "ix_role_column_rule_role_id_column_id",
            "role_id",
            "column_id",
            unique=True,
            postgresql_where=sql_text("role_id IS NOT NULL"),
        ),
        Index(
            "ix_role_column_rule_project_id_column_id",
            "project_id",
            "column_id",
            unique=True,
            postgresql_where=sql_text("role_id IS NULL"),
        ),
        # The same deferred composite key the rest of this module uses, and
        # satisfied trivially by a baseline row — see `ProjectPermission`.
        ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_role_column_rule_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    """Which role the rule is about, or :data:`EVERYONE_ELSE`."""

    column_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("board_column.id", ondelete="CASCADE"),
        nullable=False,
    )
    """Plain cascade rather than the composite key above: a column already
    belongs to a project by its own foreign key, and deleting a column should
    take the rules about it with it without anybody being asked."""

    may_enter: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("true")
    )
    """Whether a card may be moved into this column by a holder of the role."""

    may_stage: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=sql_text("true")
    )
    """Whether they may set the sub-stages a card passes through here — both
    the template's policy for this column and a card's own progress bar while
    it is in it."""


class RoleClearance(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """How sensitive a thing a role may read on this project.

    A **restriction**, like the rule above: a role with no row is cleared for
    everything, which is what every project was before levels existed. See
    :mod:`app.core.sensitivity` for what the levels mean and why there are
    three.

    One row per role rather than a column on ``project_role``, for the reason
    the grants are a table: the baseline — everybody here with no role, and
    everybody not here — needs a clearance too, and it is not a role.
    """

    __tablename__ = "role_clearance"
    __table_args__ = (
        Index(
            "ix_role_clearance_role_id",
            "role_id",
            unique=True,
            postgresql_where=sql_text("role_id IS NOT NULL"),
        ),
        Index(
            "ix_role_clearance_project_id",
            "project_id",
            unique=True,
            postgresql_where=sql_text("role_id IS NULL"),
        ),
        ForeignKeyConstraint(
            ["project_id", "role_id"],
            ["project_role.project_id", "project_role.id"],
            name="fk_role_clearance_project_id_role_id_project_role",
            ondelete="NO ACTION",
            deferrable=True,
            initially="DEFERRED",
        ),
    )

    project_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="CASCADE"),
        nullable=False,
    )
    role_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))

    level: Mapped[str] = mapped_column(String(16), nullable=False)
    """The highest :class:`~app.core.sensitivity.Sensitivity` this role reads.

    Text rather than a database enum, for the reason a permission is: adding a
    level should be a release, and a level this server has never heard of
    should degrade to a documented fallback rather than to a 500.
    """
