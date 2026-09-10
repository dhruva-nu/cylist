"""Agent sessions: which Claude Code session is on which card, and how it stands.

An agent working a card is a fact about *now* that nothing else on the board
records. The activity trail says what an agent did once it has done it; the
task's ``status`` is about the work, not about who is at the keyboard; and
``waiting_on`` is a list of people. None of them can say "something is on
this card this minute, and it has a question for you" — which is what a
board is glanced at for while an agent runs.

One row per (task, client session). The client session is the harness's own
id for the conversation — Claude Code's ``session_id`` — so two terminals on
the same card are two rows, and the card shows the one that needs a human
first (see :func:`app.services.agent_sessions.presence`).

The rows are written by lifecycle hooks, not by the model: the harness
reports working / waiting / done deterministically, and the agent itself has
no tool to lie with. A row that stops hearing from its hook is ``working``
forever in the database and *stale* in the read model, which is computed
rather than stored so a crashed process needs no reaper to be told about.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID

from sqlalchemy import CheckConstraint, DateTime, Enum, ForeignKey, Index, String, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPrimaryKeyMixin


class AgentSessionState(StrEnum):
    """What the session is doing, as far as its hooks have said."""

    WORKING = "working"
    """A turn is in progress: the agent is thinking or calling tools."""

    WAITING = "waiting"
    """The turn ended, or the harness stopped to ask something. Nothing
    happens until a human types — see ``reason`` for which."""

    DONE = "done"
    """The session ended, or moved to another card. Stays on the card, greyed
    as finished, until somebody dismisses it or touches the card."""


class AgentSessionReason(StrEnum):
    """Why a session is waiting, or why it is done."""

    TURN_ENDED = "turn_ended"
    """The agent answered and is waiting for the next prompt."""

    PERMISSION = "permission"
    """The harness is asking whether a tool may run."""

    IDLE = "idle"
    """The harness has been waiting on a prompt for a while."""

    QUESTION = "question"
    """The agent asked a question of its own. Reserved for a later
    ``ask_human`` tool; no hook sends it today."""

    MOVED = "moved"
    """The same client session bound to another card, ending this row."""

    SESSION_ENDED = "session_ended"
    """The harness exited, or the session was unbound with ``/work off``."""

    CONNECTION_LOST = "connection_lost"
    """The agent's socket went away without a goodbye — it dropped, or it went
    quiet past the idle window. One reason for both, because a card cannot
    show the difference; which it was is in the activity payload's ``cause``.

    Distinct from ``session_ended``, which is a session that said it was
    leaving. This one is the board noticing on its own."""


def _enum(python_type: type[StrEnum], name: str) -> Enum:
    """A VARCHAR of the values, never a native PG enum — see ``models/task.py``.

    No check constraint comes with it: ``create_constraint`` is left at its
    default of ``False``, so the members are enforced here and not by the
    database. What the database does keep is the *width* — the column is a
    ``VARCHAR`` sized to the longest member — so a new member longer than
    every existing one still needs a migration to widen it, as
    ``connection_lost`` did in revision 0022.
    """
    return Enum(
        python_type,
        name=name,
        native_enum=False,
        values_callable=lambda enum: [member.value for member in enum],
    )


class AgentSession(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "agent_session"
    __table_args__ = (
        # One row per conversation per card. A session that comes back to a
        # card it left reopens its old row rather than starting a second one.
        Index(
            "ix_agent_session_task_id_client_session_id",
            "task_id",
            "client_session_id",
            unique=True,
        ),
        # What the board reads: the open rows of a set of cards. Ended rows are
        # read too, until dismissed, but the open ones are the hot path — every
        # heartbeat and every presence read goes through this index.
        Index(
            "ix_agent_session_task_id_open",
            "task_id",
            postgresql_where=sql_text("ended_at IS NULL"),
        ),
        # Done is a moment, and the moment is the state: neither "done with no
        # time" nor "working with an end time" can be written at all.
        CheckConstraint(
            "(state = 'done') = (ended_at IS NOT NULL)",
            name="done_has_ended_at",
        ),
    )

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )

    token_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("api_token.id", ondelete="SET NULL"),
    )
    """The agent's credential — the token behind the hook's PUTs. Nulled
    rather than cascaded if the token is deleted: the record that an agent was
    here outlives the key it used."""

    actor_label: Mapped[str] = mapped_column(String(120), nullable=False)
    """The token's name when the row was first written, kept for the same
    reason ``Activity.actor_label`` is."""

    client_session_id: Mapped[str] = mapped_column(String(200), nullable=False)
    """The harness's own id for the conversation: Claude Code's
    ``session_id``. Opaque here; only ever compared for equality."""

    client_name: Mapped[str | None] = mapped_column(String(200))
    """The session's display name, when the hook knows it. Usually the task
    reference, since binding renames the session to it."""

    state: Mapped[AgentSessionState] = mapped_column(
        _enum(AgentSessionState, "agent_session_state"), nullable=False
    )

    reason: Mapped[AgentSessionReason | None] = mapped_column(
        _enum(AgentSessionReason, "agent_session_reason")
    )
    """Why it is waiting, or why it is done. Null while working."""

    note: Mapped[str | None] = mapped_column(Text)
    """Free text — the question an agent asked, once there is a tool to ask
    one with. Nothing writes it today."""

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When ``state`` last changed. Distinct from ``last_seen_at``, which a
    heartbeat refreshes without changing anything: "working since 14:02" is
    this, "last heard from at 14:31" is that."""

    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """Every PUT refreshes it, and so does any write the same token makes to
    the card. Silence past a threshold is what the read model calls stale."""

    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set exactly when ``state`` is done — see ``done_has_ended_at``."""

    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When a finished session was cleared off the card, by the Dismiss button
    or by a person touching the card in the web UI. A dismissed row is history;
    the board no longer draws it."""

    @property
    def is_open(self) -> bool:
        return self.ended_at is None
