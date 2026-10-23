"""The rule between a card and the sub-tasks under it, held under a lock.

A card in the board's last column has nothing open under it. That rule is
asked from both ends — by the card arriving there, and by a sub-task being
written or reopened under it — and each end used to read the other's state
and then write its own. Two requests at the same moment could both read
"fine" and commit a finished card over an open sub-task, which nothing then
repairs: the board has no way back for a done card with open children.

So both ends lock the parent's row first, and read what they decide on only
once they hold it. Whichever comes second waits for the first to commit and
then sees what it wrote.

The late write is refused rather than allowed to reopen the parent. A
sub-task cannot be added or reopened under a finished card: the refusal
names the card, and the way through is to move that card out of the last
column first — which reopens it in its own history, said by somebody who
meant to. Quietly reopening a finished card as a side effect of ticking a box
on something under it would put a card back on the board that nobody moved.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import Reason, UnprocessableRequestError
from app.models.task import Task


async def lock_parent(session: AsyncSession, parent_id: UUID) -> datetime | None:
    """Lock a card's row until the transaction ends, and say when it was finished.

    ``FOR NO KEY UPDATE`` rather than ``FOR UPDATE``: the two ends of the rule
    take the same lock, so they still take turns, but a request merely writing
    a row that points at the card — a comment, a history entry — is not made to
    wait for it.

    The time is read by the locking statement itself, so it is the committed
    value as of the lock and not whatever the session loaded at the start of
    the request: that stale copy is exactly what the race was made of.
    """
    finished_at: datetime | None = await session.scalar(
        select(Task.finished_at).where(Task.id == parent_id).with_for_update(key_share=True)
    )
    return finished_at


async def refuse_open_under_finished(session: AsyncSession, parent: Task, *, act: str) -> None:
    """Refuse to leave a sub-task open under ``parent`` if it is finished.

    Called before a sub-task is written or reopened, with the parent locked by
    :func:`lock_parent` for the rest of the transaction, so a move of the
    parent into the last column waits for this to commit and then finds the
    sub-task open. ``act`` names what was refused, as the sentence's object —
    "splitting more work out of it", "reopening ATL-41-2".

    Raises:
        UnprocessableRequestError: naming the parent, if it is finished.
    """
    if await lock_parent(session, parent.id) is None:
        return
    raise UnprocessableRequestError(
        f"{parent.reference} is finished, so nothing under it can be open. Move "
        f"{parent.reference} out of the board's last column first — restoring it to the "
        f"board if it is in cold storage — and then try {act} again.",
        details={"parent": parent.reference},
        reason=Reason.PARENT_FINISHED,
    )
