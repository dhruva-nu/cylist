"""Ringing the board when anything on a project is written.

A board goes stale for *any* change, not only the agent ones, and a push wired
to presence alone would quietly stop showing a card that an MCP ``move_task``
had moved; the ten-second poll used to carry those. So every recorded entry on
a project rings its boards' doorbell, naming what changed — see
:mod:`app.realtime.hub` — and nothing more.

A subscriber to the activity trail, registered by ``create_app``.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db import publish_into
from app.realtime.events import BoardChanged
from app.services.activity import Recorded


async def ring(session: AsyncSession, recorded: Recorded) -> None:
    """Queue a ``board.changed`` for the entry's project, if it belongs to one."""
    entry = recorded.entry
    if entry.project_id is None:
        return
    publish_into(
        session,
        BoardChanged(
            project_id=entry.project_id,
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            verb=entry.verb,
        ),
    )
