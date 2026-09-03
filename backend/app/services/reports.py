"""Turning a day of the audit trail into something a person can read.

Nothing here records anything. Every fact in a day's report is already in the
``activity`` table, written by whichever service performed the change; this
reads that back, cuts it at the boundaries of one local day, and groups it by
the card it happened to.

Two decisions are worth knowing about.

**A day is local.** The trail stores UTC, but "what did I do today" is asked
about the day the asker just lived, so the window is midnight to midnight in a
named zone — see :func:`window`. That also makes the answer stable: a report
for last Tuesday is cut the same way whenever it is asked for, DST included.

**The wording is the trail's own.** Every line comes from
:func:`app.services.activity.describe`, so a card's history and this report
never word the same event differently, and there is one place to fix when they
would.

**A day of moves is one move.** A card dragged To do → In progress → Dev
between breakfast and dinner is, by the end of the day, a card that got from
To do to Dev; the columns it passed through on the way are not three things
somebody did. So the report collapses a card's moves to where it started and
where it ended — see :func:`_condensed`. The card's own history, which is the
full record, still keeps every one of them.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import UnprocessableRequestError
from app.models.activity import Activity
from app.models.project import Project
from app.models.task import Task
from app.schemas.activity import FieldChange, HistoryEntry
from app.schemas.reports import DayReport, TaskDay
from app.services import activity, columns, tasks

UTC_ZONE = ZoneInfo("UTC")


def zone_for(name: str | None) -> ZoneInfo:
    """The zone a day is being asked about; UTC when the caller does not say.

    UTC as the default rather than the server's own zone: a deployment's
    ``TZ`` is an accident of how the container was built, and a report that
    silently changes shape when the image is rebuilt is worse than one that is
    obviously in UTC until a client says otherwise.

    Raises:
        UnprocessableRequestError: if the name is not one the system knows.
    """
    if name is None:
        return UTC_ZONE
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnprocessableRequestError(
            f"{name!r} is not a time zone this server knows. Give an IANA name, "
            "such as 'Asia/Kolkata' or 'UTC'.",
            details={"timezone": name},
        ) from exc


def today_in(zone: ZoneInfo) -> date:
    """The date it is right now, where the caller is."""
    return datetime.now(zone).date()


def window(day: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    """Midnight to midnight around ``day`` in ``zone``, as UTC instants.

    Built from two local midnights rather than from one plus 24 hours, so the
    day a clock change falls on is the 23 or 25 hours it actually was.
    """
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    return start.astimezone(UTC), end.astimezone(UTC)


async def for_day(
    session: AsyncSession,
    project: Project,
    *,
    day: date,
    zone: ZoneInfo,
) -> DayReport:
    """Everything one project's day amounted to."""
    starts_at, ends_at = window(day, zone)
    entries = await activity.between(session, project.id, starts_at, ends_at)

    board = await columns.list_for_project(session, project)
    last_column = board[-1].id if board else None
    column_names = {column.id: column.name for column in board}
    # Keyed by the string a payload stores, for the moves too old to have
    # recorded a column's name themselves — see `activity.describe`.
    named = {str(column.id): column.name for column in board}
    cards = {card.reference: card for card in await tasks.list_for_project(session, project)}

    # Insertion order is chronological, because the entries are: a day reads
    # in the order its cards were first picked up.
    by_card: dict[str, list[Activity]] = {}
    elsewhere: list[HistoryEntry] = []
    for entry in entries:
        reference = _card_reference(entry)
        if reference is None:
            elsewhere.append(activity.entry_of(entry, named))
        else:
            by_card.setdefault(reference, []).append(entry)

    touched = [
        _task_day(reference, rows, cards, column_names, last_column, named)
        for reference, rows in by_card.items()
    ]
    finished = [card.reference for card in touched if card.finished]
    # What the report holds, rather than what the trail does: a card's day of
    # moves has been collapsed to one line by now, and a count that disagreed
    # with the lines under it would just look like a bug.
    shown = sum(len(card.entries) for card in touched) + len(elsewhere)

    headline = _headline(
        changes=shown,
        cards=len(touched),
        finished=len(finished),
        elsewhere=len(elsewhere),
        day=day,
    )
    return DayReport(
        project_key=project.key,
        project_name=project.name,
        day=day,
        timezone=str(zone),
        starts_at=starts_at,
        ends_at=ends_at,
        entry_count=shown,
        finished=finished,
        tasks=touched,
        elsewhere=elsewhere,
        headline=headline,
        markdown=_markdown(
            project=project,
            day=day,
            zone=zone,
            headline=headline,
            tasks=touched,
            elsewhere=elsewhere,
        ),
    )


def _card_reference(entry: Activity) -> str | None:
    """The card an entry happened to, or None if it was not about a card.

    Read from the payload rather than from ``entity_id``, because a deleted
    card has no id left to group by and its last day is the one most worth
    reporting.
    """
    if entry.entity_type != "task":
        return None
    reference = entry.payload.get("reference")
    return reference if isinstance(reference, str) else None


def _task_day(
    reference: str,
    rows: list[Activity],
    cards: dict[str, Task],
    column_names: dict[UUID, str],
    last_column: UUID | None,
    named: dict[str, str],
) -> TaskDay:
    """One card's entries, with where the card stands now."""
    card = cards.get(reference)
    entries = _condensed([activity.entry_of(row, named) for row in rows])
    finished = _ended_in(rows, last_column)

    if card is None:
        # Deleted, or created under a reference the board no longer holds. The
        # title it had at the time is in the payload of whichever entry named
        # one; failing that the reference is all there is to call it.
        title = next(
            (str(row.payload["title"]) for row in rows if row.payload.get("title")),
            reference,
        )
        return TaskDay(
            reference=reference,
            title=title,
            column=None,
            status=None,
            finished=finished,
            entries=entries,
        )

    return TaskDay(
        reference=reference,
        title=card.title,
        column=column_names.get(card.column_id),
        status=card.status,
        finished=finished,
        entries=entries,
    )


def _ended_in(rows: list[Activity], column_id: UUID | None) -> bool:
    """Whether the card's last move of the day put it in ``column_id``.

    The last move rather than any move: a card dropped in Done and pulled back
    out an hour later did not finish today, whatever the middle of the
    afternoon looked like. Same reading of the day as :func:`_condensed` — what
    the card ended up as, not everywhere it has been.
    """
    if column_id is None:
        return False
    arrived = next((row for row in reversed(rows) if row.verb == "task.moved"), None)
    return arrived is not None and arrived.payload.get("column_id") == str(column_id)


def _condensed(entries: list[HistoryEntry]) -> list[HistoryEntry]:
    """One card's day with its moves collapsed to the one they amounted to.

    To do → In progress → Dev is a card that got from To do to Dev; the column
    it stopped in on the way is where it was passing through, not something
    that was done to it. The collapsed line keeps the last move's moment and
    actor — the day's work was finished when the card arrived — and is reworded
    to name the column the day started in.

    Only moves collapse. Two edits an hour apart are two things done, and a day
    that reported them as one would be hiding half of itself.

    A card that went somewhere and came back keeps a line of its own rather
    than being dropped: it is one line either way, and a day where nothing is
    said about a card that moved reads as a day the card sat still.
    """
    moves = [entry for entry in entries if entry.verb == "task.moved"]
    if len(moves) < 2:
        return entries

    first, last = moves[0], moves[-1]
    started_in, _ = _column_ends(first)
    _, ended_in = _column_ends(last)
    kept = [entry for entry in entries if entry.verb != "task.moved" or entry is last]

    if ended_in is None:
        # A move recorded before CYLIST-8 kept the destination's id and neither
        # column's name, so there is no pair of ends to restate. The last
        # move's own line stands for the day, and the ones before it still go.
        return kept

    collapsed = last.model_copy(
        update={
            "summary": activity.moved(started_in, ended_in),
            "changes": [
                FieldChange(field="column", label="column", before=started_in, after=ended_in),
                # A move can also put a card back to its first sub-status, and
                # that is still true of the collapsed one.
                *[change for change in last.changes if change.field != "column"],
            ],
            "payload": {**last.payload, "moves": len(moves)},
        }
    )
    return [collapsed if entry is last else entry for entry in kept]


def _column_ends(entry: HistoryEntry) -> tuple[str | None, str | None]:
    """The columns a move was between, as it recorded them.

    Both None for a move written before CYLIST-8: those rows kept the
    destination's id and neither column's name, which is not enough to restate
    one end of a day against another.
    """
    change = next((change for change in entry.changes if change.field == "column"), None)
    if change is None:
        return None, None
    return (
        str(change.before) if change.before else None,
        str(change.after) if change.after else None,
    )


def _headline(*, changes: int, cards: int, finished: int, elsewhere: int, day: date) -> str:
    """The day in one sentence, for the top of the report."""
    if changes == 0:
        return f"Nothing was recorded on {_long_date(day)}."

    said = f"{changes} {_plural(changes, 'change')}"
    if cards:
        said += f" across {cards} {_plural(cards, 'card')}"
        if finished:
            said += f", {finished} of them finished"
        if elsewhere:
            said += f", and {elsewhere} elsewhere on the project"
    elif elsewhere:
        said += " elsewhere on the project"
    return f"{said}."


def _markdown(
    *,
    project: Project,
    day: date,
    zone: ZoneInfo,
    headline: str,
    tasks: list[TaskDay],
    elsewhere: list[HistoryEntry],
) -> str:
    """The report as Markdown, ready to paste into a stand-up note.

    Written here rather than by each client so the note a person copies out of
    the browser and the one an agent writes are the same text.
    """
    lines = [f"# {project.name} — {_long_date(day)}", "", headline]

    finished = [card for card in tasks if card.finished]
    if finished:
        lines += ["", "## Finished", ""]
        lines += [f"- **{card.reference}** {card.title}" for card in finished]

    if tasks:
        lines += ["", "## Cards", ""]
        for card in tasks:
            where = f" ({card.column})" if card.column else " (deleted)"
            lines += [f"### {card.reference} — {card.title}{where}", ""]
            lines += [_line(entry, zone) for entry in card.entries]
            lines.append("")
        lines.pop()

    if elsewhere:
        lines += ["", "## Elsewhere", ""]
        lines += [_line(entry, zone) for entry in elsewhere]

    # A trailing newline, so appending this to a longer note does not run the
    # last line into whatever follows it.
    return "\n".join(lines) + "\n"


def _line(entry: HistoryEntry, zone: ZoneInfo) -> str:
    """One event as a bullet: when, what, and who if it was not a person.

    An agent's work is named because the report is read as an account of a
    person's day, and an entry a bot wrote is not part of it.
    """
    when = entry.occurred_at.astimezone(zone).strftime("%H:%M")
    if entry.channel == "api":
        return f"- {when} · {entry.summary} — {entry.actor_label} (agent)"
    return f"- {when} · {entry.summary}"


def _long_date(day: date) -> str:
    """``Wednesday 2 September 2026``, without a platform-specific format code."""
    return f"{day:%A} {day.day} {day:%B %Y}"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
