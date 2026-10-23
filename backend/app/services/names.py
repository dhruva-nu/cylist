"""Claiming a name that must be unique within some scope.

Goals, templates, hooks, vault trees, doc topics and folder items each refuse a
second one of the same name, and each used to carry its own copy of the same
two steps: look for a clash so the refusal can say who has the name, then, on
the write itself, turn the unique index's refusal into the same answer — the
index is what decides between two requests that both passed the look.

The look is :func:`ensure_free`. The index half is
:func:`app.db.flush_or_refuse`, which wants the refusal built up front;
:func:`taken` is how a service builds it, so the pre-check and the index say
the same thing in the same words.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy import Select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import ConflictError, Reason


def taken(message: str, **details: Any) -> ConflictError:
    """The refusal for a name somebody else already has."""
    return ConflictError(message, details=details, reason=Reason.DUPLICATE_NAME)


async def ensure_free[Clash](
    session: AsyncSession,
    clash: Select[tuple[Clash]],
    refusal: Callable[[Clash], ConflictError],
) -> None:
    """Refuse if ``clash`` finds anything, handing what it found to ``refusal``.

    ``clash`` selects whatever already holds the name — the row, or just its
    id when the message does not need more. The refusal receives it so the
    sentence can quote the name as the holder spelt it, not as it was typed.

    Raises:
        ConflictError: the one ``refusal`` builds.
    """
    found = await session.scalar(clash.limit(1))
    if found is not None:
        raise refusal(found)
