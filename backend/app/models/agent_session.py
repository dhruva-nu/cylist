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
no tool to lie with.

A row is only ever ``working`` while something says so. That used to be
untrue, and the read model papered over it: nothing ended the row of a
process that had died, so a *stale* state was computed from how long it had
been silent. It worked, and it cost the board the ability to ever be pushed
to — a card changed what it said with no event behind it, so a client had to
keep asking. Three things end a row now, and all of them are events: the
agent's socket closing, the sweep on startup (a process holding no sockets
has no live sessions, whatever the rows say), and the reaper, for the
clients that hold no socket to close.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Identity,
    Index,
    String,
    Text,
)
from sqlalchemy import text as sql_text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
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
    """The agent asked a question of its own with the ``ask_human`` tool and
    is holding its turn open for the answer — see :class:`AgentPrompt`."""

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
    """A VARCHAR of the values plus a CHECK, never a native PG enum — see
    ``models/task.py``.

    The CHECK arrived in revision 0031. Before it the members were enforced
    here alone and the database kept only the *width* — which is why
    ``connection_lost`` needed revision 0022 to widen the column and nothing
    else. A new member now needs a migration whatever its length, one that
    replaces ``ck_agent_session_<name>`` and widens the column if it has to.
    """
    return Enum(
        python_type,
        name=name,
        native_enum=False,
        create_constraint=True,
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
    """One line on what the session is doing right now, as its last report
    said: "Running pytest test_pr_states.py", "Editing tasks.py". The hook
    writes it on every tool call, and every report replaces it — so it is
    current or it is absent, never history. Cleared when the session ends."""

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    state_changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When ``state`` last changed. Distinct from ``last_seen_at``, which a
    heartbeat refreshes without changing anything: "working since 14:02" is
    this, "last heard from at 14:31" is that."""

    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """Every report refreshes it, and so does any write the same token makes
    to the card.

    No longer load-bearing for what a card *shows* — the border is a function
    of ``state`` alone. It is what the reaper reads when deciding whether a
    session that holds no socket has been quiet long enough to stop believing
    in, and it is what a person reads to know how long ago that was."""

    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """Set exactly when ``state`` is done — see ``done_has_ended_at``."""

    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When a finished session was cleared off the card, by the Dismiss button
    or by a person touching the card in the web UI. A dismissed row is history;
    the board no longer draws it."""


class AgentFeedKind(StrEnum):
    """What one line of a session's feed is."""

    SAID = "said"
    """Something the agent wrote to the person it is working for."""

    READ = "read"
    """A tool call that looks: reading a file, searching, fetching a page."""

    EDIT = "edit"
    """A tool call that changes a file."""

    RUN = "run"
    """A shell command."""

    TOOL = "tool"
    """Any other tool call — an MCP tool, a subagent, a to-do list."""

    OUTPUT = "output"
    """The last line a shell command printed, as the agent saw it."""

    ERROR = "error"
    """A tool call that failed, with the first line of why."""


class AgentFeedEntry(Base, UUIDPrimaryKeyMixin):
    """One line of what a session read, edited, ran and said — CYLIST-88.

    Written by the agent's presence daemon, which follows the session's
    Claude Code transcript and sends what it finds down the socket the
    session already holds. Kept rather than only pushed past, so that a card
    opened halfway through a session shows how it got there, and so that the
    feed survives a reload — but only the newest
    :data:`app.services.agent_feed.KEEP_PER_SESSION` lines of each session:
    this is a window on the work, not an archive of it, and the activity trail
    is still the record.

    The text is redacted on the agent's machine before it is sent (see the
    CLI's ``presence/narrate.py``). Nothing here is a plaintext secret by
    design; nothing here is guaranteed not to be one, which is why it is
    readable only by people who can already read the card.

    Ordered by ``seq`` rather than by ``at`` or ``id``: ``at`` is the agent's
    clock, and a UUIDv7 orders only across milliseconds — the lines of one
    frame are written in the same one and must still come back in the order
    they were sent.
    """

    __tablename__ = "agent_feed_entry"
    __table_args__ = (Index("ix_agent_feed_entry_task_id_seq", "task_id", "seq"),)

    seq: Mapped[int] = mapped_column(BigInteger, Identity(), nullable=False, unique=True)
    """Insertion order, drawn by the database."""

    agent_session_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("agent_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
    )
    """The session's card, copied here so a card's feed is one index scan
    rather than a join through every session that was ever on it."""

    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the agent did it, by the transcript's clock."""

    kind: Mapped[AgentFeedKind] = mapped_column(
        _enum(AgentFeedKind, "agent_feed_kind"), nullable=False
    )

    tool: Mapped[str | None] = mapped_column(String(200))
    """The tool's own name — ``Bash``, ``Edit``, ``mcp__cylist__get_task`` —
    for every kind but ``said``."""

    text: Mapped[str] = mapped_column(Text, nullable=False)


class AgentPromptKind(StrEnum):
    """What an agent is waiting on a person for."""

    QUESTION = "question"
    """The agent asked something with the ``ask_human`` tool — CYLIST-85. Its
    turn is held open until a person answers, so the answer comes back as the
    tool's result and the agent carries on in the same turn."""

    PERMISSION = "permission"
    """Claude Code is asking whether a tool may run — CYLIST-89. The harness's
    ``PermissionRequest`` hook asked the session's presence daemon, which put
    it on the card; the terminal's own prompt is open at the same time, and
    whichever is answered first wins."""


class AgentPromptStatus(StrEnum):
    """Where a prompt has got to. Only ``open`` is waiting on anyone."""

    OPEN = "open"
    ANSWERED = "answered"
    """A person answered it on the board, and the agent was told."""

    EXPIRED = "expired"
    """Nobody answered before ``expires_at``. A question's tool returns saying
    so, and the agent decides on its own or asks in the terminal."""

    WITHDRAWN = "withdrawn"
    """The agent stopped waiting before anyone here answered: the prompt was
    answered in the terminal, the tool call was interrupted, or the session
    ended. Which, when it is known, is in ``closed_reason``."""


class AgentPrompt(Base, UUIDPrimaryKeyMixin):
    """One thing an agent session is waiting on a person for: a question it
    asked, or a permission the harness wants — CYLIST-85 and CYLIST-89.

    One model for both because the board treats them alike: the card turns
    amber, says what is being asked, offers the choices, and whatever a person
    picks goes back to the agent. They differ in how the answer travels — a
    question's tool long-polls for it over HTTP, a permission's hook is
    waiting on the presence daemon, which is told over the agent's socket —
    and that difference lives in :mod:`app.services.agent_prompts`, not here.

    A session's row carries its state; this carries what it is waiting *on*.
    A prompt that is no longer open stays, so the card can say "You: Allow
    once" after the fact and the task panel can list what was asked.
    """

    __tablename__ = "agent_prompt"
    __table_args__ = (
        # The hot read: a card's open prompts, for the board and the panel.
        Index(
            "ix_agent_prompt_agent_session_id_open",
            "agent_session_id",
            postgresql_where=sql_text("status = 'open'"),
        ),
        # Open is the absence of an end, written both ways so neither can drift.
        CheckConstraint(
            "(status = 'open') = (closed_at IS NULL)",
            name="open_has_no_closed_at",
        ),
        CheckConstraint(
            "(status = 'answered') = (answered_at IS NOT NULL)",
            name="answered_has_answered_at",
        ),
    )

    agent_session_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("agent_session.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    task_id: Mapped[UUID] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("task.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    """The session's card, copied here so a card's prompts need no join."""

    kind: Mapped[AgentPromptKind] = mapped_column(
        _enum(AgentPromptKind, "agent_prompt_kind"), nullable=False
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)
    """The question, or for a permission what the tool wants to do:
    "Run git push origin staging". Redacted on the agent's machine for a
    permission; a question is the agent's own words."""

    tool: Mapped[str | None] = mapped_column(String(200))
    """For a permission, the tool's own name — ``Bash``, ``Edit``."""

    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
    """The choices, in order, as ``{"key", "label"}``. For a permission they
    are always the three in :data:`app.services.agent_prompts.PERMISSION_OPTIONS`;
    for a question whatever the agent offered, possibly none."""

    status: Mapped[AgentPromptStatus] = mapped_column(
        _enum(AgentPromptStatus, "agent_prompt_status"), nullable=False
    )

    answer: Mapped[str | None] = mapped_column(Text)
    """What was answered, as words: the chosen option's label, or the reply a
    person typed. What a card shows after "You:"."""

    answer_key: Mapped[str | None] = mapped_column(String(40))
    """The chosen option's key, or null for a typed reply."""

    answered_by_id: Mapped[UUID | None] = mapped_column(
        postgresql.UUID(as_uuid=True),
        ForeignKey("person.id", ondelete="SET NULL"),
    )

    answered_by_label: Mapped[str | None] = mapped_column(String(120))
    """Who answered, by the name they had then — kept for the reason
    ``Activity.actor_label`` is."""

    closed_reason: Mapped[str | None] = mapped_column(String(40))
    """Why a withdrawn prompt was withdrawn: ``answered_in_terminal``,
    ``interrupted``, ``session_ended``, ``abandoned``."""

    asked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """After this nobody may answer it, and the next look at it expires it."""

    last_polled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    """When the agent last showed it was still waiting: a question's tool
    polling, or the session's socket carrying the permission. A question
    nobody has polled for :data:`app.services.agent_prompts.ABANDONED_AFTER`
    is one whose tool call is gone."""

    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    """When it stopped being open, for whatever reason."""
