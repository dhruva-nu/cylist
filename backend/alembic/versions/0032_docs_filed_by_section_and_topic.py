"""Docs: a project's markdown, filed as section → topic → doc.

Two tables, one permission and one project's starting topics.

``doc_topic`` is a project's own heading under one of the two fixed sections,
``product`` and ``engineering`` — an enum checked by the database from the
start, as 0031 made every enum. ``doc`` is a markdown body filed under exactly
one topic, carrying its project beside the topic so a composite key keeps it
on its own project's topics. That key refuses rather than cascades: the
service will not delete a topic that still holds docs, and neither will this.

**The ``docs`` permission goes to whoever could already change files.** A
permission is a grant — no row means no — so a new word added to the
vocabulary is one every narrowed role, and every baseline seeded before today,
does not hold. Writing a doc is closest to adding a file, so each role (and
each baseline) that holds ``files`` is given ``docs`` too, the way 0026 handed
the split-out goal permissions to everybody who held ``goals``. The admin role
holds nothing in the table and needs nothing here.

**CYLIST starts with its topics**, the ones its card names — Engineering: MCP,
CLI, APIs, FE, DB schema; Product: Goals, Tasks, Agent docs. Only where a
project with that key exists, so every other database is untouched, and only
names not already there.

Revision ID: 0032
Revises: 0031
Created: 2026-09-26
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0032"
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_SECTIONS = ("product", "engineering")
"""Mirrors ``app.models.doc.DocSection`` as of this revision."""

_SEEDED_PROJECT = "CYLIST"
_SEEDED_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("product", ("Goals", "Tasks", "Agent docs")),
    ("engineering", ("MCP", "CLI", "APIs", "FE", "DB schema")),
)


def _timestamps() -> list[sa.Column[object]]:
    return [
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    ]


def upgrade() -> None:
    op.create_table(
        "doc_topic",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section", sa.String(length=11), nullable=False),
        sa.Column("name", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        *_timestamps(),
        sa.CheckConstraint(
            f"section IN ({', '.join(repr(section) for section in _SECTIONS)})",
            name=op.f("ck_doc_topic_doc_section"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_doc_topic_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doc_topic")),
        sa.UniqueConstraint("project_id", "id", name=op.f("uq_doc_topic_project_id_id")),
    )
    op.create_index(
        "ix_doc_topic_project_id_section_name",
        "doc_topic",
        ["project_id", "section", "name"],
        unique=True,
    )

    op.create_table(
        "doc",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("topic_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("author_id", postgresql.UUID(as_uuid=True), nullable=True),
        *_timestamps(),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_doc_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id", "topic_id"],
            ["doc_topic.project_id", "doc_topic.id"],
            name="fk_doc_project_id_topic_id_doc_topic",
        ),
        sa.ForeignKeyConstraint(
            ["author_id"],
            ["person.id"],
            name=op.f("fk_doc_author_id_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_doc")),
    )
    op.create_index("ix_doc_topic_id_position", "doc", ["topic_id", "position"])
    op.create_index("ix_doc_project_id", "doc", ["project_id"])

    _grant_docs_where_files_was()
    _seed_cylist_topics()


def _grant_docs_where_files_was() -> None:
    op.get_bind().execute(
        sa.text(
            """
            INSERT INTO project_permission (id, project_id, role_id, permission)
            SELECT gen_random_uuid(), held.project_id, held.role_id, 'docs'
              FROM project_permission AS held
             WHERE held.permission = 'files'
            ON CONFLICT DO NOTHING
            """
        )
    )


def _seed_cylist_topics() -> None:
    connection = op.get_bind()
    for section, names in _SEEDED_TOPICS:
        for position, name in enumerate(names):
            connection.execute(
                sa.text(
                    """
                    INSERT INTO doc_topic (id, project_id, section, name, position)
                    SELECT gen_random_uuid(), project.id, :section, :name, :position
                      FROM project
                     WHERE project.key = :key
                    ON CONFLICT DO NOTHING
                    """
                ),
                {"key": _SEEDED_PROJECT, "section": section, "name": name, "position": position},
            )


def downgrade() -> None:
    op.get_bind().execute(sa.text("DELETE FROM project_permission WHERE permission = 'docs'"))
    op.drop_index("ix_doc_project_id", table_name="doc")
    op.drop_index("ix_doc_topic_id_position", table_name="doc")
    op.drop_table("doc")
    op.drop_index("ix_doc_topic_project_id_section_name", table_name="doc_topic")
    op.drop_table("doc_topic")
