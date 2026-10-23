"""What the goals services return.

Plain values the services build and hand back. The HTTP layer re-exports them
(``app.schemas.goals``) as the responses its routes declare, so there is one definition
and the services import nothing from the HTTP layer.
"""

from __future__ import annotations

from pydantic import Field

from app.services.readmodels.base import ReadModel


class GoalProgress(ReadModel):
    """How far along a goal is, counted from the cards linked to it.

    Derived rather than stored: a goal's progress is a reading of its cards,
    and a percentage kept in a column beside them is a number that can be
    wrong. Cards only — a sub-task belongs to its card, and its card is what
    the goal counts.
    """

    total: int = Field(description="Cards linked to this goal, cancelled ones included.")
    done: int = Field(
        description="Cards in the board's last column. What 'done' means for a card is where "
        "it is, so it is where this is counted from."
    )
    cancelled: int = Field(description="Cards dropped. Settled, but not achieved.")
    open: int = Field(
        description="Cards that are neither done nor cancelled — what is left. While this is "
        "above zero the goal cannot be marked achieved."
    )
    blocked: int = Field(description="Open cards that cannot proceed.")
    on_hold: int = Field(description="Open cards deliberately paused.")
