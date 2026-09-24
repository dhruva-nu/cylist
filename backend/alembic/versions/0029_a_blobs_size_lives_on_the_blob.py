"""A blob's size lives on the blob, and nowhere else.

**Two copies go.** ``file_item.size`` and ``skill.size`` were copied from
``blob.size`` at upload "so a listing needs no join" — but both models load
their blob with ``lazy="joined"``, so every listing made the join anyway and
read the copy instead of the thing beside it. A blob is addressed by the
SHA-256 of its bytes, so its size cannot change; a copy of it can only ever be
right or wrong. Both models now read ``size`` off the blob, and the API says
exactly what it said.

**One column nothing read goes.** ``blob.mime`` was "the type the first upload
of these bytes declared". Every reader takes the type from the row that refers
to the blob — ``file_item.mime``, ``skill.mime`` — because the bytes are shared
and the label is not: ``cutover.md`` and ``cutover.txt`` are one blob and two
types. Nothing read the blob's own, so it could only mislead the next person
to look.

**A copy that disagreed with its blob.** Nothing in the application could make
one, but the upgrade does not assume so. It logs every file item and skill
whose copy disagrees, then drops the copy: the blob's size is the length of
the bytes on disk, so it is the true one, and the API reports that from now
on. Drift is a wrong number being corrected, not data being lost; the log is
there so that a changed number in a listing has an explanation. A *link* with
a size — which no code path writes either — is logged the same way, since a
link now reports none.

**The downgrade rebuilds all three** from what is left: each copy from its
blob, and the blob's own type from the earliest row still pointing at it,
which is what "the first upload" meant — or ``application/octet-stream``, the
upload default, where a first upload's row has since been deleted with no
other left. A drifted copy comes back as the blob's size rather than as the
wrong number it was.

Revision ID: 0029
Revises: 0028
Created: 2026-09-25
"""

from __future__ import annotations

import logging
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

log = logging.getLogger(f"alembic.revision.{revision}")

_DEFAULT_MIME = "application/octet-stream"
"""Mirrors ``app.services.blobs.DEFAULT_MIME``."""

_DISAGREEING = sa.text(
    "SELECT 'file_item', file_item.id, file_item.name, file_item.size, blob.size"
    " FROM file_item LEFT JOIN blob ON blob.id = file_item.blob_id"
    " WHERE file_item.size IS DISTINCT FROM blob.size"
    " UNION ALL"
    " SELECT 'skill', skill.id, skill.name, skill.size, blob.size"
    " FROM skill JOIN blob ON blob.id = skill.blob_id"
    " WHERE skill.size <> blob.size"
    " ORDER BY 1, 3"
)
"""Every copy that says something other than its blob.

For a link ``blob.size`` is NULL through the outer join, so a link is listed
only when it carries a size of its own."""


def upgrade() -> None:
    for table, row_id, name, copied, actual in op.get_bind().execute(_DISAGREEING):
        log.warning(
            "%s %s (%r) said %s bytes; its blob holds %s. Reporting the blob's from now on.",
            table,
            row_id,
            name,
            copied,
            actual,
        )

    op.drop_column("file_item", "size")
    op.drop_column("skill", "size")
    op.drop_column("blob", "mime")


def downgrade() -> None:
    op.add_column("blob", sa.Column("mime", sa.String(length=255), nullable=True))
    op.execute(
        "UPDATE blob SET mime = earliest.mime FROM ("
        "  SELECT DISTINCT ON (blob_id) blob_id, mime FROM ("
        "    SELECT blob_id, mime, created_at FROM file_item"
        "     WHERE blob_id IS NOT NULL AND mime IS NOT NULL"
        "    UNION ALL"
        "    SELECT blob_id, mime, created_at FROM skill"
        "  ) AS referrer"
        "  ORDER BY blob_id, created_at"
        ") AS earliest WHERE earliest.blob_id = blob.id"
    )
    op.execute(
        sa.text("UPDATE blob SET mime = :default WHERE mime IS NULL").bindparams(
            default=_DEFAULT_MIME
        )
    )
    op.alter_column("blob", "mime", existing_type=sa.String(length=255), nullable=False)

    op.add_column("file_item", sa.Column("size", sa.BigInteger(), nullable=True))
    op.execute("UPDATE file_item SET size = blob.size FROM blob WHERE blob.id = file_item.blob_id")

    op.add_column("skill", sa.Column("size", sa.BigInteger(), nullable=True))
    op.execute("UPDATE skill SET size = blob.size FROM blob WHERE blob.id = skill.blob_id")
    op.alter_column("skill", "size", existing_type=sa.BigInteger(), nullable=False)
