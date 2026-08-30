"""Give every project a root folder.

Until now a project's root was implicit: ``folder.parent_id IS NULL`` meant
"top level" and no row stood for the project itself. Because
``file_item.folder_id`` is NOT NULL, that made it impossible to put a README or
a project brief at the top of a project — you had to invent a folder first.

This revision makes the root a real row, and makes "exactly one per project" a
fact of the schema: ``ix_folder_project_id_root`` is unique on ``project_id``
over precisely the parentless rows. A boolean flag would have said the same
thing twice and left room for the two answers to disagree; with this index,
"parentless" and "root" cannot come apart.

The back-fill has to be right for a database that already holds folders and
files, so the order matters:

1. The old unique index on ``(project_id, name)`` over top-level rows goes
   first. A project named *Exports* that already has a top-level folder called
   *Exports* would otherwise collide with its own new root.
2. Each project gets a root named after it.
3. Everything that *was* top level is re-parented under that root. Names stay
   unique, because folders that were unique among a project's top-level rows
   are now unique among one parent's children.
4. Only then does the new index go on, over data that already satisfies it.

Revision ID: 0006
Revises: 0005
Created: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# The application's id generator, so a back-filled root is indistinguishable
# from one the service created. Alembic already depends on `app` — env.py
# imports the models to compare against — so this adds no new coupling.
from app.core.ids import uuid7

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)

_INSERT_ROOT = sa.text(
    "INSERT INTO folder (id, project_id, parent_id, name) VALUES (:id, :project_id, NULL, :name)"
).bindparams(
    sa.bindparam("id", type_=_UUID),
    sa.bindparam("project_id", type_=_UUID),
    sa.bindparam("name", type_=sa.String()),
)
"""``created_at`` and ``updated_at`` are left to their server defaults, so a
back-filled root is stamped by the same clock as every other row."""

_REPARENT = sa.text("UPDATE folder SET parent_id = :parent_id WHERE id = :id").bindparams(
    sa.bindparam("id", type_=_UUID),
    sa.bindparam("parent_id", type_=_UUID),
)


def upgrade() -> None:
    op.drop_index("ix_folder_project_id_name", table_name="folder")

    connection = op.get_bind()

    # Read this before inserting anything: once the roots exist, "parentless"
    # no longer picks out the folders that used to be at the top.
    was_top_level = connection.execute(
        sa.text("SELECT id, project_id FROM folder WHERE parent_id IS NULL")
    ).all()

    roots: list[dict[str, Any]] = [
        {"id": uuid7(), "project_id": project_id, "name": name}
        for project_id, name in connection.execute(sa.text("SELECT id, name FROM project"))
    ]

    if roots:
        connection.execute(_INSERT_ROOT, roots)

        root_of: dict[UUID, UUID] = {root["project_id"]: root["id"] for root in roots}
        reparented = [
            {"id": folder_id, "parent_id": root_of[project_id]}
            for folder_id, project_id in was_top_level
        ]
        if reparented:
            connection.execute(_REPARENT, reparented)

    op.create_index(
        "ix_folder_project_id_root",
        "folder",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )


def downgrade() -> None:
    connection = op.get_bind()

    # Files at the top of a project are the whole point of this revision, and
    # the shape below it has nowhere to put them: dropping the roots would take
    # them along by cascade. Refusing is the only honest answer.
    stranded = connection.execute(
        sa.text(
            "SELECT count(*) FROM file_item"
            " JOIN folder ON folder.id = file_item.folder_id"
            " WHERE folder.parent_id IS NULL"
        )
    ).scalar_one()
    if stranded:
        raise RuntimeError(
            f"{stranded} file(s) sit directly in a project root, which the schema before "
            "revision 0006 cannot represent. Move them into a folder before downgrading."
        )

    # Name the roots now. After the next statement their children are
    # parentless too, and the two would be indistinguishable.
    root_ids = list(
        connection.scalars(sa.text("SELECT id FROM folder WHERE parent_id IS NULL")).all()
    )

    # The index saying only one folder per project may be parentless has to come
    # off before the roots' children go back to being parentless themselves.
    op.drop_index("ix_folder_project_id_root", table_name="folder")

    if root_ids:
        lift = (
            sa.text("UPDATE folder SET parent_id = NULL WHERE parent_id = ANY(:root_ids)")
            .bindparams(sa.bindparam("root_ids", type_=postgresql.ARRAY(_UUID)))
            .bindparams(root_ids=root_ids)
        )
        connection.execute(lift)
        connection.execute(
            sa.text("DELETE FROM folder WHERE id = ANY(:root_ids)")
            .bindparams(sa.bindparam("root_ids", type_=postgresql.ARRAY(_UUID)))
            .bindparams(root_ids=root_ids)
        )

    op.create_index(
        "ix_folder_project_id_name",
        "folder",
        ["project_id", "name"],
        unique=True,
        postgresql_where=sa.text("parent_id IS NULL"),
    )
