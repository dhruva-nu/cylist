"""Audit trail.

Every mutation writes one row. Because agents act through the same API as the
browser, this is the only place that answers "what did the CLI change at 3am?".
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Index, String, func
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class Channel(StrEnum):
    """Which door the actor came through."""

    WEB = "web"
    API = "api"


class EntityType(StrEnum):
    """The kind of thing an activity row is about.

    The writer's side of :attr:`Activity.entity_type`. The column stays plain
    text, because the trail outlives the code that wrote it: a row recorded
    under a name this enum has since dropped is still read back.
    """

    COLUMN = "column"
    DOC = "doc"
    DOC_TOPIC = "doc_topic"
    FOLDER = "folder"
    GOAL = "goal"
    HOOK = "hook"
    ITEM = "item"
    PERSON = "person"
    PROJECT = "project"
    ROLE = "role"
    SESSION = "session"
    SKILL = "skill"
    TASK = "task"
    TEMPLATE = "template"
    TOKEN = "token"
    VAULT_NODE = "vault_node"
    VAULT_TREE = "vault_tree"


class Verb(StrEnum):
    """Every event the trail records, as ``<thing>.<what happened>``.

    The one list. A verb is written here before anything records it, which is
    what lets the rest be checked against it: every verb must have a sentence
    in :mod:`app.services.activity.wording` (a test says so), and a typo at a
    call site is a type error instead of a row that fires no hook and reads
    as "Created." in a history. The column stays plain text, for
    :class:`EntityType`'s reason: retired verbs are still in old rows.
    """

    AGENT_SESSION_ANSWERED = "agent_session.answered"
    AGENT_SESSION_FINISHED = "agent_session.finished"
    AGENT_SESSION_STARTED = "agent_session.started"
    AGENT_SESSION_UNANSWERED = "agent_session.unanswered"
    AGENT_SESSION_WAITING = "agent_session.waiting"

    COLUMN_CREATED = "column.created"
    COLUMN_DELETED = "column.deleted"
    COLUMN_REORDERED = "column.reordered"
    COLUMN_UPDATED = "column.updated"

    DOC_APPENDED = "doc.appended"
    DOC_CREATED = "doc.created"
    DOC_DELETED = "doc.deleted"
    DOC_MOVED = "doc.moved"
    DOC_REORDERED = "doc.reordered"
    DOC_UPDATED = "doc.updated"

    DOC_TOPIC_CREATED = "doc_topic.created"
    DOC_TOPIC_DELETED = "doc_topic.deleted"
    DOC_TOPIC_RENAMED = "doc_topic.renamed"
    DOC_TOPIC_REORDERED = "doc_topic.reordered"

    FILE_UPLOADED = "file.uploaded"

    FOLDER_CREATED = "folder.created"
    FOLDER_DELETED = "folder.deleted"
    FOLDER_UPDATED = "folder.updated"

    GOAL_CREATED = "goal.created"
    GOAL_DELETED = "goal.deleted"
    GOAL_UPDATED = "goal.updated"

    HOOK_CREATED = "hook.created"
    HOOK_DELETED = "hook.deleted"
    HOOK_FIRED = "hook.fired"
    HOOK_SECRET_ROTATED = "hook.secret_rotated"
    HOOK_UPDATED = "hook.updated"

    ITEM_DELETED = "item.deleted"
    ITEM_UPDATED = "item.updated"

    LINK_ADDED = "link.added"

    MEMBER_ROLE_SET = "member.role_set"

    PERSON_ACCOUNT_OPENED = "person.account_opened"
    PERSON_ADDED = "person.added"
    PERSON_ARCHIVED = "person.archived"
    PERSON_INVITE_WITHDRAWN = "person.invite_withdrawn"
    PERSON_INVITED = "person.invited"
    PERSON_PASSWORD_CHANGED = "person.password_changed"
    PERSON_PASSWORD_RESET = "person.password_reset"
    PERSON_RESTORED = "person.restored"
    PERSON_UPDATED = "person.updated"

    PROJECT_ARCHIVED = "project.archived"
    PROJECT_CREATED = "project.created"
    PROJECT_DELETED = "project.deleted"
    PROJECT_MEMBERS_CHANGED = "project.members_changed"
    PROJECT_RESTORED = "project.restored"
    PROJECT_UPDATED = "project.updated"

    ROLE_CLEARANCE_SET = "role.clearance_set"
    ROLE_COLUMNS_SET = "role.columns_set"
    ROLE_CREATED = "role.created"
    ROLE_DELETED = "role.deleted"
    ROLE_PERMISSIONS_SET = "role.permissions_set"
    ROLE_UPDATED = "role.updated"

    SESSION_ENDED = "session.ended"
    SESSION_STARTED = "session.started"

    SKILL_DELETED = "skill.deleted"
    SKILL_REPLACED = "skill.replaced"
    SKILL_UPDATED = "skill.updated"
    SKILL_UPLOADED = "skill.uploaded"

    TASK_ARCHIVED = "task.archived"
    TASK_CHECKLIST_ADDED = "task.checklist_added"
    TASK_CHECKLIST_DELETED = "task.checklist_deleted"
    TASK_CHECKLIST_UPDATED = "task.checklist_updated"
    TASK_COMMENT_DELETED = "task.comment_deleted"
    TASK_COMMENTED = "task.commented"
    TASK_CREATED = "task.created"
    TASK_DELETED = "task.deleted"
    TASK_FINISHED = "task.finished"
    TASK_MOVED = "task.moved"
    TASK_RESTORED = "task.restored"
    TASK_STATUS_CHANGED = "task.status_changed"
    TASK_SUB_STATUS_MOVED = "task.sub_status_moved"
    TASK_SUBTASK_CREATED = "task.subtask_created"
    TASK_UPDATED = "task.updated"

    TEMPLATE_CREATED = "template.created"
    TEMPLATE_DELETED = "template.deleted"
    TEMPLATE_UPDATED = "template.updated"

    TOKEN_ISSUED = "token.issued"
    TOKEN_REVOKED = "token.revoked"

    VAULT_NODE_CREATED = "vault.node_created"
    VAULT_NODE_DELETED = "vault.node_deleted"
    VAULT_NODE_MOVED = "vault.node_moved"
    VAULT_NODE_UPDATED = "vault.node_updated"
    VAULT_SECRET_REVEALED = "vault.secret_revealed"
    VAULT_TREE_CREATED = "vault.tree_created"
    VAULT_TREE_DELETED = "vault.tree_deleted"
    VAULT_TREE_UPDATED = "vault.tree_updated"


class Activity(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "activity"
    __table_args__ = (
        Index("ix_activity_occurred_at", "occurred_at"),
        Index("ix_activity_project_id_occurred_at", "project_id", "occurred_at"),
        Index("ix_activity_entity_type_entity_id", "entity_type", "entity_id"),
        Index("ix_activity_actor_person_id_occurred_at", "actor_person_id", "occurred_at"),
        # Only the rows waiting for the courier, which are few and recent.
        Index(
            "ix_activity_hooks_pending",
            "occurred_at",
            "id",
            postgresql_where=sql_text("hooks_pending"),
        ),
    )

    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    actor_token_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("api_token.id", ondelete="SET NULL"),
    )
    actor_person_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )
    """Which person was behind the change, when one was.

    The token id alone stopped being enough the moment more than one person
    could hold one: "what did the CLI change at 3am" is now also "and whose
    CLI was it". Nulled rather than cascaded for the reason
    :attr:`project_id` is — the record of what happened outlives the
    directory entry — and null for the bootstrap session, which has no person.
    """

    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    """Readable actor name, kept even if the token row is later deleted.

    For a person's own session this is their name, so a feed read years later
    still says who did it without a join that may no longer resolve.
    """

    channel: Mapped[Channel] = mapped_column(
        Enum(
            Channel,
            name="channel",
            native_enum=False,
            create_constraint=True,
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )

    verb: Mapped[str] = mapped_column(String(64), nullable=False)
    """Dotted past-tense event name, e.g. ``task.status_changed``."""

    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))

    project_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("project.id", ondelete="SET NULL"),
    )
    """Scopes the feed to one project. Nulled rather than cascaded if a project
    ever goes away, so the record of what happened outlives it."""

    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    hooks_pending: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=sql_text("false")
    )
    """Whether the hooks still have to be matched against this change. Set by the
    write that records it, cleared by the courier when it has queued whatever
    deliveries the change earned — see :func:`app.services.hooks.match_pending`."""


class ActivityArchive(Base):
    """Trail rows old enough to have been moved off the hot table — CYLIST-139.

    Every mutation writes an ``activity`` row and nothing ever removed one, so
    the table that every card's history and every day report reads grew by
    every agent turn, forever. Rows older than
    :attr:`app.config.Settings.activity_retention_days` are moved here, whole,
    by :func:`app.services.retention.archive_activity`.

    Cold on purpose: nothing in the application reads it, so it carries no
    index but its key, and no foreign keys — the record of what happened
    outlives the token, person and project it names, which is the same promise
    the hot table makes by nulling them, kept here by never pointing at them at
    all. ``channel`` is plain text for the same reason: a row archived under
    one set of channels must still be insertable after that set changes.
    """

    __tablename__ = "activity_archive"

    id: Mapped[UUID] = mapped_column(postgresql.UUID(as_uuid=True), primary_key=True)
    """The hot row's own id, kept so an archived row can be matched to
    anything that recorded it — a hook delivery's log, say."""

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_token_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    actor_person_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    channel: Mapped[str] = mapped_column(String(16), nullable=False)
    verb: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(32), nullable=False)
    entity_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    project_id: Mapped[UUID | None] = mapped_column(postgresql.UUID(as_uuid=True))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    archived_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
