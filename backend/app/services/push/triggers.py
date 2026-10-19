"""Noticing that an agent session changed, wherever it was changed from.

A session's state is written in several places — a hook's report
(:func:`app.services.agent_sessions.upsert`), an agent asking a question
(:mod:`app.services.agent_prompts`), an answer resuming it, a socket dropping
(:mod:`app.services.agent_reports`), the reaper — and a push has to follow
every one of them. Rather than a call in each, which the next writer would
forget, this listens on the :class:`AgentSession` mapper itself: whenever a
row is inserted or updated, its before and after are compared with
:func:`app.services.push.messages.worth_pushing`, and a change worth telling
is queued on the session's outbox.

The outbox (:data:`app.db.OUTBOX`) is what makes this safe: it is drained only
after the transaction commits, so a change that rolls back is never pushed and
one that commits is pushed after another connection can read it. Several
flushes in one transaction queue one event per session, updated to the newest
state, so a report that walks a session through two states in one request
sends what it ended on.

Imported for its side effect by :mod:`app.services.agent_sessions`, the module
every writer of these rows already imports.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import event, inspect
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapper, Session, object_session

from app.db import publish_into
from app.models.agent_session import AgentSession
from app.services.push.messages import SessionSnapshot, worth_pushing

QUEUED = "push.agent_session"
"""The outbox event type the application's publisher hands to the pusher."""

_PENDING = "push_pending"
"""Key under ``session.info``: this transaction's queued events, by session."""


def snapshot(row: AgentSession) -> SessionSnapshot:
    """The row as it stands now."""
    return SessionSnapshot(
        agent_session_id=row.id,
        task_id=row.task_id,
        token_id=row.token_id,
        state=row.state,
        reason=row.reason,
        note=row.note,
        client_name=row.client_name,
        started_at=row.started_at,
        state_changed_at=row.state_changed_at,
    )


def _previous(row: AgentSession) -> SessionSnapshot:
    """The row as it stood before this flush, from SQLAlchemy's history."""
    state = inspect(row)

    def before(name: str) -> Any:
        history = state.attrs[name].history
        return history.deleted[0] if history.deleted else getattr(row, name)

    return SessionSnapshot(
        agent_session_id=row.id,
        task_id=before("task_id"),
        token_id=before("token_id"),
        state=before("state"),
        reason=before("reason"),
        note=before("note"),
        client_name=before("client_name"),
        started_at=before("started_at"),
        state_changed_at=before("state_changed_at"),
    )


def _queue(row: AgentSession, before: SessionSnapshot | None) -> None:
    session = object_session(row)
    if session is None:  # pragma: no cover - a flush always has a session
        return
    after = snapshot(row)
    pending: dict[UUID, dict[str, Any]] = session.info.setdefault(_PENDING, {})
    queued = pending.get(row.id)
    if queued is not None:
        # Already on the outbox, which holds this very dict: whatever this
        # flush changed, the push carries the state the transaction ends on.
        queued["snapshot"] = after
    elif worth_pushing(before, after):
        queued = {"type": QUEUED, "snapshot": after}
        pending[row.id] = queued
        publish_into(session, queued)


@event.listens_for(AgentSession, "after_insert")
def _inserted(_mapper: Mapper[AgentSession], _connection: Connection, row: AgentSession) -> None:
    _queue(row, None)


@event.listens_for(AgentSession, "after_update")
def _updated(_mapper: Mapper[AgentSession], _connection: Connection, row: AgentSession) -> None:
    _queue(row, _previous(row))


def queued_in(session: Session) -> list[SessionSnapshot]:
    """What this transaction has queued so far — for tests."""
    pending: dict[UUID, dict[str, Any]] = session.info.get(_PENDING, {})
    return [entry["snapshot"] for entry in pending.values()]
