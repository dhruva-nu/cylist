"""What a push says: an agent session's state, as an FCM data message.

Pure functions, so the payload can be tested without a database or a network.

**Data messages only**, never FCM ``notification`` messages. The app builds
the notification itself (CYLIST-107), because the same session has to be
updated in place — working, then needs you, then finished — and an Android 16
Live Update can only be posted by the app. A ``notification`` message would be
drawn by the system while the app is in the background, as a separate,
un-updatable notification.

**When a session is worth a push** is :func:`worth_pushing`: a session that
starts, changes state, or — while waiting — changes why or what it asks.
A working session's note changes on every tool call; that is not pushed,
because the phone shows elapsed time, not a running commentary, and a push per
tool call would drain a battery for nothing.

Every value is a string, because FCM data payloads are a ``map<string,
string>``; an absent one is ``""``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID

from app.models.agent_session import AgentSessionReason, AgentSessionState

KIND = "agent_session"
"""The ``kind`` every message here carries, so the app can tell this kind of
push from any added later."""

NOTE_LIMIT = 500
"""Characters of a note (a question, a permission, what the agent is doing)
carried in a push. FCM's whole data payload must fit in 4 KB."""

TITLE_LIMIT = 200
"""A card's title is at most this long already; the limit restates it."""

TTL_WORKING = "600s"
"""A "working" push stale by ten minutes is better dropped: the phone would
show a pill for a turn that has long since ended."""

TTL_SETTLED = "86400s"
"""A "needs you" or "finished" push is still news a day later."""


@dataclass(frozen=True)
class SessionSnapshot:
    """An agent session as it stood the moment it changed.

    Taken in the transaction that changed it (see
    :mod:`app.services.push.triggers`) rather than when the push is sent, so
    a session that goes working → waiting → working over three quick
    requests sends each state, not three copies of the last one.
    """

    agent_session_id: UUID
    task_id: UUID
    token_id: UUID | None
    state: AgentSessionState
    reason: AgentSessionReason | None
    note: str | None
    client_name: str | None
    started_at: datetime
    state_changed_at: datetime


@dataclass(frozen=True)
class CardContext:
    """The card a session is on, as a notification names it."""

    reference: str
    title: str
    project_key: str


def worth_pushing(before: SessionSnapshot | None, after: SessionSnapshot) -> bool:
    """Whether a change to a session is news to the phone.

    ``before`` is ``None`` for a session just created.
    """
    if before is None or before.state is not after.state:
        return True
    if after.state is AgentSessionState.WAITING:
        return before.reason is not after.reason or before.note != after.note
    return False


def data_for(snapshot: SessionSnapshot, card: CardContext) -> dict[str, str]:
    """The FCM ``data`` map for one session on one card."""
    return {
        "kind": KIND,
        "session_id": str(snapshot.agent_session_id),
        "task": card.reference,
        "task_title": _clip(card.title, TITLE_LIMIT),
        "project": card.project_key,
        "state": snapshot.state.value,
        "reason": snapshot.reason.value if snapshot.reason else "",
        "note": _clip(snapshot.note or "", NOTE_LIMIT),
        "client_name": snapshot.client_name or "",
        "started_at": snapshot.started_at.isoformat(),
        "state_changed_at": snapshot.state_changed_at.isoformat(),
    }


def message_for(fcm_token: str, snapshot: SessionSnapshot, card: CardContext) -> dict[str, Any]:
    """One FCM v1 ``message`` object, addressed to one device.

    High priority for every state: each one changes what the phone shows at
    once — a pill appears, a question rings, a pill goes away — and Android
    only wakes an app in Doze for a high-priority message. The app always
    posts or updates a notification for one, which is what keeps FCM from
    demoting it.

    ``collapse_key`` is the session, so a phone that was offline is handed
    only that session's newest state when it comes back.
    """
    return {
        "token": fcm_token,
        "data": data_for(snapshot, card),
        "android": {
            "priority": "HIGH",
            "ttl": TTL_WORKING if snapshot.state is AgentSessionState.WORKING else TTL_SETTLED,
            "collapse_key": str(snapshot.agent_session_id),
        },
    }


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
