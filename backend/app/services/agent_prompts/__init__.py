"""What an agent session is waiting on a person for, and the person's answer.

Two ways an agent ends up waiting on a person in the middle of a turn, and
one model for both — :class:`~app.models.agent_session.AgentPrompt`:

* A **question** (CYLIST-85). The agent calls the MCP tool ``ask_human``,
  which :func:`ask`\\ s here and then long-polls the prompt's ``GET`` route
  until it closes; the answer is the tool's result, so the agent carries on
  in the same turn.
* A **permission** (CYLIST-89). Claude Code's ``PermissionRequest`` hook asks
  the session's presence daemon, the daemon sends a ``prompt`` frame down the
  socket it already holds, and :func:`open_permission` puts it on the card.
  The answer goes back down the same socket as an ``answer`` frame. The
  terminal's own dialog is open at the same time, and the first answer wins:
  the terminal winning is how a permission is *withdrawn*.

Whichever it is, a person answers with :func:`answer`, the session goes back
to ``working``, the answer is kept on the prompt so a card can say "You:
Allow once", and the activity trail gets a line.

How the news travels is the part to read twice. Every close publishes an
``agent.prompt`` event through :func:`app.db.publish_into`, so it goes out
only once the transaction commits. :meth:`app.realtime.hub.Hub.publish` turns
it into three things: the board's doorbell, a wake-up for any long-poll
waiting on that prompt, and — for a permission — the ``answer`` frame on the
agent's socket. A wake-up that is missed costs nothing but latency: a waiter
re-reads the row when its wait ends, and the daemon re-sends an unanswered
``prompt`` after a reconnect, which this module answers with the outcome.

The package is laid out by who closes a prompt: :mod:`.asking` opens them,
:mod:`.answering` closes one on purpose, :mod:`.settling` closes the ones
nobody closed on purpose, and :mod:`.reading` reads them back. The moves all
of those share — reading a row locked, closing it, announcing it — are in
:mod:`.store`.
"""

from app.services.agent_prompts.answering import POLL_WAIT_MAX, answer, wait_state, withdraw
from app.services.agent_prompts.asking import (
    PERMISSION_OPTIONS,
    PERMISSION_WAIT,
    ask,
    open_permission,
)
from app.services.agent_prompts.reading import for_sessions, read, recent, shown_for
from app.services.agent_prompts.settling import (
    ABANDONED_AFTER,
    settle_for_report,
    sweep,
    withdraw_for_sessions,
)
from app.services.agent_prompts.store import SESSION_ENDED

__all__ = [
    "ABANDONED_AFTER",
    "PERMISSION_OPTIONS",
    "PERMISSION_WAIT",
    "POLL_WAIT_MAX",
    "SESSION_ENDED",
    "answer",
    "ask",
    "for_sessions",
    "open_permission",
    "read",
    "recent",
    "settle_for_report",
    "shown_for",
    "sweep",
    "wait_state",
    "withdraw",
    "withdraw_for_sessions",
]
