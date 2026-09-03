"""A day's report: what happened on one project, on one day."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import Field

from app.models.task import TaskStatus
from app.schemas.activity import HistoryEntry
from app.schemas.common import Schema


class TaskDay(Schema):
    """One card's share of a day: everything that happened to it, in order."""

    reference: str = Field(description="`ATL-41`, or `ATL-41-2` for a sub-task.")
    title: str = Field(
        description="The card's title now — or the one it had at the time, if it "
        "has since been deleted."
    )
    column: str | None = Field(
        description="Which column the card sits in now. Null if it no longer exists."
    )
    status: TaskStatus | None = Field(description="Its status now. Null if it no longer exists.")
    finished: bool = Field(
        description="Whether the card's last move of the day put it in the board's "
        "last column. False for one dropped there and pulled back out again."
    )
    entries: list[HistoryEntry] = Field(
        description="What happened to it, oldest first. Its moves appear as the one "
        "move they amounted to — from the column the day started in to the one it "
        "ended in — carrying `moves` in the payload to say how many there were."
    )


class DayReport(Schema):
    """What one project's day amounted to, ready to be read or pasted.

    Grouped by card rather than left as a flat feed, because a day spent on
    four cards is four pieces of work and thirty audit rows — the grouping is
    the part that turns the record into a report. Condensed for the same
    reason: a card's moves collapse to where it started and where it ended, so
    a card walked across the board reads as one step rather than four.
    """

    project_key: str
    project_name: str
    day: date = Field(description="The day this reports on, in `timezone`.")
    timezone: str = Field(description="The IANA zone the day was cut by, e.g. `Asia/Kolkata`.")
    starts_at: datetime = Field(description="Midnight that began the day, in UTC.")
    ends_at: datetime = Field(
        description="Midnight that ended it, in UTC. Exclusive: an entry at "
        "exactly this moment belongs to the next day."
    )
    entry_count: int = Field(
        description="How many lines the report holds — a card's day of moves counting "
        "as the one move it amounted to, not as each drag."
    )
    finished: list[str] = Field(
        description="References of the cards that ended the day in the board's last column."
    )
    tasks: list[TaskDay] = Field(
        description="The cards touched today, in the order they were first touched."
    )
    elsewhere: list[HistoryEntry] = Field(
        description="Changes that were not to a card — files, columns, the vault, "
        "the project itself — oldest first."
    )
    headline: str = Field(description="The day in one sentence.")
    markdown: str = Field(
        description="The whole report as Markdown, worded by the server so a "
        "stand-up note pasted from the browser and one written by an agent read "
        "the same."
    )
