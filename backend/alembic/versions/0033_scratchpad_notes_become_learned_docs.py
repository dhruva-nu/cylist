"""The agent scratchpad is retired: each note moves into a topic's learned.md.

What an agent learns now goes into the project's docs, beside everything else
written about the project, rather than onto a list of its own. So every line
still on the scratchpad is filed into one of its project's topics and added to
that topic's ``learned.md`` — made at the bottom of the topic if it has none —
and ``agent_note`` is dropped.

**Which topic is jev's call, as it is for a doc an agent writes today**: one
Choice over the project's topics, each described by the docs it already holds
(see ``app.services.doc_judge.filing_question``, which this mirrors as of this
revision). Unlike a live write there is nobody to ask when jev is unsure, so
its first choice stands. A project with one topic needs no question; a project
with none, or a note jev cannot be asked about — no ``JEV_API_KEY``, or no
answer — goes to an *Engineering → Agents* topic, made if it is not there. A
line is never dropped for want of somewhere to put it.

Each note keeps who wrote it and when, as a list item:
``- <note> — <author>, <date>``, oldest first, so a ``learned.md`` reads in the
order things were learned.

The undo recreates the empty table. The notes stay where they were filed: they
are docs now, and a person may already have edited them.

Revision ID: 0033
Revises: 0032
Created: 2026-09-26
"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any
from uuid import UUID

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op
from app.config import get_settings
from app.core.ids import uuid7

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

_LEARNED = "learned.md"
_FALLBACK_SECTION = "engineering"
_FALLBACK_TOPIC = "Agents"
_SECTION_LABELS = {"product": "Product", "engineering": "Engineering"}
_SECTION_ORDER = ("product", "engineering")
_NOTE_MAX_LENGTH = 280


class _Topic:
    def __init__(self, topic_id: UUID, section: str, name: str, doc_titles: list[str]) -> None:
        self.id = topic_id
        self.section = section
        self.name = name
        self.doc_titles = doc_titles

    @property
    def label(self) -> str:
        return f"{_SECTION_LABELS[self.section]} / {self.name}"


def upgrade() -> None:
    connection = op.get_bind()
    notes = connection.execute(
        sa.text(
            "SELECT project_id, body, author_label, created_at FROM agent_note"
            " ORDER BY created_at, id"
        )
    ).all()

    by_project: dict[UUID, list[Any]] = defaultdict(list)
    for note in notes:
        by_project[note.project_id].append(note)

    if by_project:
        with _jev() as jev:
            for project_id, project_notes in by_project.items():
                _file_notes(connection, jev, project_id, project_notes)

    op.drop_index("ix_agent_note_project_id_created_at", table_name="agent_note")
    op.drop_table("agent_note")


def _file_notes(connection: Any, jev: Any, project_id: UUID, notes: list[Any]) -> None:
    topics = _topics(connection, project_id)
    lines: dict[UUID, list[str]] = defaultdict(list)
    for note in notes:
        topic_id = _choose(jev, note.body, topics)
        if topic_id is None:
            topic_id = _fallback_topic(connection, project_id, topics)
        lines[topic_id].append(
            f"- {note.body} — {note.author_label}, {note.created_at.date().isoformat()}"
        )
    for topic_id, entries in lines.items():
        _add_to_learned(connection, project_id, topic_id, entries)


def _topics(connection: Any, project_id: UUID) -> list[_Topic]:
    rows = connection.execute(
        sa.text(
            "SELECT id, section, name FROM doc_topic WHERE project_id = :project"
            " ORDER BY position, created_at"
        ),
        {"project": project_id},
    ).all()
    titles: dict[UUID, list[str]] = defaultdict(list)
    for doc in connection.execute(
        sa.text("SELECT topic_id, title FROM doc WHERE project_id = :project ORDER BY position"),
        {"project": project_id},
    ):
        titles[doc.topic_id].append(doc.title)
    topics = [_Topic(row.id, row.section, row.name, titles[row.id]) for row in rows]
    return sorted(topics, key=lambda topic: _SECTION_ORDER.index(topic.section))


def _choose(jev: Any, note: str, topics: list[_Topic]) -> UUID | None:
    """jev's topic for one note, or None when there is no one to ask."""
    if len(topics) == 1:
        return topics[0].id
    if jev is None or not topics:
        return None

    from jev import JevError, choice

    question = choice(
        "topic",
        "Which topic of this project's docs should this new doc be filed under?",
        {
            topic.label: (
                "Already holds: " + "; ".join(topic.doc_titles[:20])
                if topic.doc_titles
                else "No docs filed here yet."
            )
            for topic in topics
        },
    )
    try:
        answer = jev.ask({"doc": {"title": _LEARNED, "body": note}}, question)[question]
    except JevError as exc:
        logger.warning("jev could not file a scratchpad note (%s); it goes to Agents", exc)
        return None
    by_label = {topic.label: topic.id for topic in topics}
    return by_label.get(answer.choice)


def _fallback_topic(connection: Any, project_id: UUID, topics: list[_Topic]) -> UUID:
    for topic in topics:
        if topic.section == _FALLBACK_SECTION and topic.name.casefold() == "agents":
            return topic.id
    position = connection.execute(
        sa.text(
            "SELECT count(*) FROM doc_topic WHERE project_id = :project AND section = :section"
        ),
        {"project": project_id, "section": _FALLBACK_SECTION},
    ).scalar_one()
    topic_id = uuid7()
    connection.execute(
        sa.text(
            "INSERT INTO doc_topic (id, project_id, section, name, position)"
            " VALUES (:id, :project, :section, :name, :position)"
        ),
        {
            "id": topic_id,
            "project": project_id,
            "section": _FALLBACK_SECTION,
            "name": _FALLBACK_TOPIC,
            "position": position,
        },
    )
    topics.append(_Topic(topic_id, _FALLBACK_SECTION, _FALLBACK_TOPIC, []))
    return topic_id


def _add_to_learned(connection: Any, project_id: UUID, topic_id: UUID, entries: list[str]) -> None:
    existing = connection.execute(
        sa.text(
            "SELECT id, body FROM doc WHERE topic_id = :topic AND lower(title) = :title"
            " ORDER BY position LIMIT 1"
        ),
        {"topic": topic_id, "title": _LEARNED},
    ).first()
    added = "\n".join(entries)
    if existing is not None:
        body = existing.body.rstrip()
        connection.execute(
            sa.text("UPDATE doc SET body = :body, updated_at = now() WHERE id = :id"),
            {"id": existing.id, "body": f"{body}\n{added}" if body else added},
        )
        return

    position = connection.execute(
        sa.text("SELECT count(*) FROM doc WHERE topic_id = :topic"), {"topic": topic_id}
    ).scalar_one()
    connection.execute(
        sa.text(
            "INSERT INTO doc (id, project_id, topic_id, title, body, position)"
            " VALUES (:id, :project, :topic, :title, :body, :position)"
        ),
        {
            "id": uuid7(),
            "project": project_id,
            "topic": topic_id,
            "title": _LEARNED,
            "body": added,
            "position": position,
        },
    )


@contextmanager
def _jev() -> Iterator[Any]:
    """A jev client when a key is configured, otherwise None."""
    settings = get_settings()
    if not settings.jev_api_key:
        logger.warning("No JEV_API_KEY: scratchpad notes are filed without asking jev")
        yield None
        return
    from jev import Jev

    with Jev(settings.jev_api_key, model=settings.jev_model) as client:
        yield client


def downgrade() -> None:
    op.create_table(
        "agent_note",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("body", sa.String(length=_NOTE_MAX_LENGTH), nullable=False),
        sa.Column("author_label", sa.String(length=120), nullable=False),
        sa.Column("added_by", postgresql.UUID(as_uuid=True), nullable=True),
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
        sa.CheckConstraint(
            f"char_length(btrim(body)) BETWEEN 1 AND {_NOTE_MAX_LENGTH}",
            name=op.f("ck_agent_note_body_is_short_and_not_blank"),
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["project.id"],
            name=op.f("fk_agent_note_project_id_project"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["added_by"],
            ["person.id"],
            name=op.f("fk_agent_note_added_by_person"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_agent_note")),
    )
    op.create_index(
        "ix_agent_note_project_id_created_at",
        "agent_note",
        ["project_id", "created_at"],
        unique=False,
    )
