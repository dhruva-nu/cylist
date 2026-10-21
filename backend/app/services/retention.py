"""How long the tables that only ever grow keep what they hold — CYLIST-139.

Every agent turn writes an ``agent_session.waiting`` line to the activity
trail, every session leaves a row with up to two hundred feed lines and its
prompts behind it, and nothing removed any of them. The queries that build
every card read those tables, so the board got slower by the month for no
reason anybody would choose.

Two rules, each with a setting of its own (zero turns it off):

* **Dismissed agent sessions** are deleted, feed and prompts with them, once
  they have been dismissed for :attr:`~app.config.Settings.agent_session_retention_days`.
  A dismissed session is one that has finished *and* been cleared off its
  card; it is drawn nowhere. An open session is never taken, whatever its
  age — the WHERE clause says ``done``, and a session that is somehow both
  dismissed and holding an open prompt is left alone as well, because a
  person may still be about to answer it.
* **Activity** older than :attr:`~app.config.Settings.activity_retention_days`
  is *moved* to ``activity_archive``, in the same statement that removes it,
  so a row is always in exactly one of the two. The trail is the audit
  record; it is taken off the hot path, never thrown away.

Run once an hour from the hook courier's prune pass, in every process.
Each batch takes its rows ``FOR UPDATE SKIP LOCKED``, so two processes
pruning at once split the work rather than tripping over it, and a session
that a report is reopening at that moment is skipped rather than deleted
under it. Batches are bounded and so is the number per pass: a deployment
turning this on for the first time drains its backlog over a few hours
instead of holding up hook deliveries for one long one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, exists, insert, select

from app.config import Settings
from app.core.clock import now
from app.db import Database
from app.models.activity import Activity, ActivityArchive
from app.models.agent_session import (
    AgentPrompt,
    AgentPromptStatus,
    AgentSession,
    AgentSessionState,
)

logger = logging.getLogger(__name__)

SESSION_BATCH = 200
"""Sessions deleted per statement. Each may take two hundred feed lines with
it by cascade, so this is tens of thousands of rows at most."""

ACTIVITY_BATCH = 2000
"""Trail rows moved per statement."""

MAX_BATCHES = 25
"""Statements per rule per pass, at most. Whatever is left waits an hour."""


@dataclass(frozen=True)
class Retention:
    """How long each rule keeps its rows; ``None`` keeps them forever."""

    agent_sessions_for: timedelta | None = None
    activity_for: timedelta | None = None

    @classmethod
    def of(cls, settings: Settings) -> Retention:
        return cls(
            agent_sessions_for=_days(settings.agent_session_retention_days),
            activity_for=_days(settings.activity_retention_days),
        )


@dataclass(frozen=True)
class Swept:
    """What one pass did, for the log and for tests."""

    agent_sessions: int = 0
    activity: int = 0


def _days(days: int) -> timedelta | None:
    return timedelta(days=days) if days > 0 else None


async def apply(database: Database, retention: Retention, *, at: datetime | None = None) -> Swept:
    """Apply every rule that is switched on. Logged when it did anything."""
    moment = at or now()
    swept = Swept(
        agent_sessions=(
            await forget_agent_sessions(database, moment - retention.agent_sessions_for)
            if retention.agent_sessions_for is not None
            else 0
        ),
        activity=(
            await archive_activity(database, moment - retention.activity_for)
            if retention.activity_for is not None
            else 0
        ),
    )
    if swept.agent_sessions or swept.activity:
        logger.info(
            "Retention pass",
            extra={
                "context": {
                    "agent_sessions_deleted": swept.agent_sessions,
                    "activity_archived": swept.activity,
                }
            },
        )
    return swept


async def forget_agent_sessions(database: Database, dismissed_before: datetime) -> int:
    """Delete finished sessions dismissed before a moment, feed and prompts too.

    The feed and the prompts go by ``ON DELETE CASCADE``, each over an index
    on its ``agent_session_id``. Returns how many sessions went.
    """
    total = 0
    for _ in range(MAX_BATCHES):
        doomed = (
            select(AgentSession.id)
            .where(
                AgentSession.dismissed_at < dismissed_before,
                AgentSession.state == AgentSessionState.DONE,
                AgentSession.ended_at.is_not(None),
                ~exists().where(
                    AgentPrompt.agent_session_id == AgentSession.id,
                    AgentPrompt.status == AgentPromptStatus.OPEN,
                ),
            )
            .order_by(AgentSession.dismissed_at)
            .limit(SESSION_BATCH)
            .with_for_update(skip_locked=True)
        )
        async with database.session() as session:
            gone = (
                await session.scalars(
                    delete(AgentSession)
                    .where(AgentSession.id.in_(doomed))
                    .returning(AgentSession.id)
                )
            ).all()
        total += len(gone)
        if len(gone) < SESSION_BATCH:
            break
    return total


async def archive_activity(database: Database, occurred_before: datetime) -> int:
    """Move trail rows older than a moment to ``activity_archive``.

    One statement per batch — the DELETE's RETURNING feeds the INSERT — so a
    row is never in both tables and never in neither. A hook delivery that
    pointed at a moved row has its ``activity_id`` nulled by its foreign key;
    deliveries are pruned long before a row is old enough to move, so in
    practice there is none. Returns how many rows moved.
    """
    columns = [
        "id",
        "occurred_at",
        "actor_token_id",
        "actor_person_id",
        "actor_label",
        "channel",
        "verb",
        "entity_type",
        "entity_id",
        "project_id",
        "payload",
    ]
    total = 0
    for _ in range(MAX_BATCHES):
        doomed = (
            select(Activity.id)
            .where(Activity.occurred_at < occurred_before)
            .order_by(Activity.occurred_at)
            .limit(ACTIVITY_BATCH)
            .with_for_update(skip_locked=True)
        )
        moved = (
            delete(Activity)
            .where(Activity.id.in_(doomed))
            .returning(*(Activity.__table__.c[name] for name in columns))
            .cte("moved")
        )
        async with database.session() as session:
            archived = (
                await session.scalars(
                    insert(ActivityArchive)
                    .from_select(columns, select(*(moved.c[name] for name in columns)))
                    .returning(ActivityArchive.id)
                )
            ).all()
        total += len(archived)
        if len(archived) < ACTIVITY_BATCH:
            break
    return total
