"""Turning a day of the audit trail into something a person can read.

Nothing here records anything. Every fact in a day's report is already in the
``activity`` table, written by whichever service performed the change; this
reads that back, cuts it at the boundaries of one local day, and groups it by
the card it happened to.

Two decisions are worth knowing about.

**A day is local.** The trail stores UTC, but "what did I do today" is asked
about the day the asker just lived, so the window is midnight to midnight in a
named zone — see :func:`window`, and :mod:`app.core.timezones` for how loosely
that zone may be named. That also makes the answer stable: a report for last
Tuesday is cut the same way whenever it is asked for, DST included.

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

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.core import clock
from app.models.activity import Activity, EntityType, Verb
from app.models.board import BoardColumn
from app.models.project import Project
from app.models.task import Task
from app.models.today import TodoState
from app.schemas.activity import FieldChange, HistoryEntry
from app.schemas.reports import CardTodos, DayReport, DayTodo, TaskDay
from app.services import activity, columns, tasks, today


def today_in(zone: ZoneInfo) -> date:
    """The date it is right now, where the caller is."""
    return clock.today(zone)


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
    person_id: UUID | None = None,
) -> DayReport:
    """Everything one project's day amounted to.

    ``person_id`` is whoever is asking. Their own plan for the day — the cards
    they picked up and their to-dos — goes in as its own section; nobody
    else's does, because a plan is a person's own.
    """
    starts_at, ends_at = window(day, zone)
    entries = await activity.between(session, project.id, starts_at, ends_at)

    board = _Board.of(await columns.list_for_project(session, project))
    # Every task, not only the board's cards: a sub-task is not on the board but
    # it is as likely as anything else to be in a day's work.
    cards = {card.reference: card for card in await tasks.all_for_project(session, project)}

    # Insertion order is chronological, because the entries are: a day reads
    # in the order its cards were first picked up.
    by_card: dict[str, list[Activity]] = {}
    elsewhere: list[HistoryEntry] = []
    for entry in entries:
        reference = _card_reference(entry)
        if reference is None:
            elsewhere.append(activity.entry_of(entry, board.names_by_payload_id))
        else:
            by_card.setdefault(reference, []).append(entry)

    touched = [
        _task_day(reference, rows, cards.get(reference), board)
        for reference, rows in by_card.items()
    ]
    finished = [card.reference for card in touched if card.finished]
    # What the report holds, rather than what the trail does: a card's day of
    # moves has been collapsed to one line by now, and a count that disagreed
    # with the lines under it would just look like a bug.
    shown = sum(len(card.entries) for card in touched) + len(elsewhere)

    planned = await _planned(session, project, person_id, day, zone)

    todo_sentence = _todo_sentence(planned)
    # A day with a plan and no changes on the board is not a day where nothing
    # was recorded; the plan is the record, so it is the headline on its own.
    headline = (
        todo_sentence
        if shown == 0 and todo_sentence
        else " ".join(
            part
            for part in (
                _headline(
                    changes=shown,
                    cards=len(touched),
                    finished=len(finished),
                    elsewhere=len(elsewhere),
                    day=day,
                ),
                todo_sentence,
            )
            if part
        )
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
        todos=planned,
        headline=headline,
        markdown=_markdown(
            project=project,
            day=day,
            zone=zone,
            headline=headline,
            tasks=touched,
            elsewhere=elsewhere,
            planned=planned,
        ),
    )


async def _planned(
    session: AsyncSession, project: Project, person_id: UUID | None, day: date, zone: ZoneInfo
) -> list[CardTodos]:
    """The asker's plan for the day, as the report shows it."""
    if person_id is None:
        return []
    # From today on the plan follows its cards, so today's report reads the
    # plan as the board has it now; a day gone by keeps what it had.
    if day >= today_in(zone):
        await today.sync_day(session, project, person_id, day)
    return [
        CardTodos(
            reference=picked.task.reference,
            title=picked.task.title,
            column=picked.task.column.name if picked.task.column else None,
            todos=[DayTodo.model_validate(todo) for todo in picked.todos],
        )
        for picked in await today.picks_for(session, project, person_id, day)
    ]


def _todo_sentence(planned: list[CardTodos]) -> str:
    """What became of the day's to-dos, as a sentence of its own.

    Empty when there were none, so a day nobody planned reads as it always did.
    """
    states = [todo.state for card in planned for todo in card.todos]
    if not states:
        return ""
    done = states.count(TodoState.DONE)
    said = f"{done} of {len(states)} {_plural(len(states), 'to-do')} done"
    moved = [todo.moved_to for card in planned for todo in card.todos if todo.moved_to]
    if moved:
        # By date, not weekday: Monday the 19th and Monday the 26th are two days.
        days = set(moved)
        where = f"{days.pop():%A}" if len(days) == 1 else "later days"
        said += f", {len(moved)} moved to {where}"
    left = states.count(TodoState.NOT_DONE)
    if left:
        said += f", {left} not done"
    return f"{said}."


def _card_reference(entry: Activity) -> str | None:
    """The card an entry happened to, or None if it was not about a card.

    Read from the payload rather than from ``entity_id``, because a deleted
    card has no id left to group by and its last day is the one most worth
    reporting.
    """
    if entry.entity_type != EntityType.TASK:
        return None
    reference = entry.payload.get("reference")
    return reference if isinstance(reference, str) else None


@dataclass(frozen=True)
class _Board:
    """What a day's report needs to know about the board it is reporting on."""

    names: dict[UUID, str]
    names_by_payload_id: dict[str, str]
    """The same names keyed by the string a payload stores, for the moves too
    old to have recorded a column's name themselves — see
    :func:`app.services.activity.describe`."""
    last_column_id: UUID | None

    @classmethod
    def of(cls, columns_left_to_right: list[BoardColumn]) -> _Board:
        return cls(
            names={column.id: column.name for column in columns_left_to_right},
            names_by_payload_id={str(column.id): column.name for column in columns_left_to_right},
            last_column_id=columns_left_to_right[-1].id if columns_left_to_right else None,
        )


def _task_day(reference: str, rows: list[Activity], card: Task | None, board: _Board) -> TaskDay:
    """One card's entries, with where the card stands now."""
    entries = _condensed([activity.entry_of(row, board.names_by_payload_id) for row in rows])
    finished = _ended_in(rows, board.last_column_id)

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
            parent=None,
            status=None,
            finished=finished,
            entries=entries,
        )

    return TaskDay(
        reference=reference,
        title=card.title,
        # A sub-task is in no column, and its parent is what says so: without it
        # the two ways of having no column — not on the board, and not existing
        # any more — would read as the same thing.
        column=board.names.get(card.column_id) if card.column_id else None,
        parent=card.parent.reference if card.parent else None,
        status=card.status,
        finished=finished,
        entries=entries,
    )


def _ended_in(rows: list[Activity], column_id: UUID | None) -> bool:
    """Whether the day left this task finished.

    Two kinds of task finish two different ways, so this reads whichever
    applies: a card by ending the day in the board's last column, a sub-task by
    being ticked off. Both are read from the *last* such entry rather than from
    any of them — a card dropped in Done and pulled back out an hour later did
    not finish today, whatever the middle of the afternoon looked like. Same
    reading of the day as :func:`_condensed`: what the task ended up as, not
    everywhere it has been.
    """
    settled = next(
        (row for row in reversed(rows) if row.verb in {Verb.TASK_MOVED, Verb.TASK_FINISHED}), None
    )
    if settled is None:
        return False
    if settled.verb == Verb.TASK_FINISHED:
        return bool(settled.payload.get("finished"))
    return column_id is not None and settled.payload.get("column_id") == str(column_id)


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
    moves = [entry for entry in entries if entry.verb == Verb.TASK_MOVED]
    if len(moves) < 2:
        return entries

    first, last = moves[0], moves[-1]
    started_in, _ = _column_ends(first)
    _, ended_in = _column_ends(last)
    kept = [entry for entry in entries if entry.verb != Verb.TASK_MOVED or entry is last]

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
    planned: list[CardTodos],
) -> str:
    """The report as Markdown, ready to paste into a stand-up note.

    Written here rather than by each client so the note a person copies out of
    the browser and the one an agent writes are the same text.
    """
    lines = [f"# {project.name} — {_long_date(day)}", "", headline]

    # The plan first: it is what the day was meant to hold, and the cards
    # below are what it did hold.
    if any(card.todos for card in planned):
        lines += ["", "## To-dos"]
        for plan in planned:
            if not plan.todos:
                continue
            where = f" ({plan.column})" if plan.column else ""
            lines += ["", f"### {plan.reference} — {plan.title}{where}", ""]
            lines += [_todo_line(todo) for todo in plan.todos]

    finished = [card for card in tasks if card.finished]
    if finished:
        lines += ["", "## Finished", ""]
        lines += [f"- **{card.reference}** {card.title}" for card in finished]

    if tasks:
        lines += ["", "## Cards", ""]
        for card in tasks:
            where = _where_it_stands(card)
            lines += [f"### {card.reference} — {card.title}{where}", ""]
            lines += [_bullet(entry, zone) for entry in card.entries]
            lines.append("")
        lines.pop()

    if elsewhere:
        lines += ["", "## Elsewhere", ""]
        lines += [_bullet(entry, zone) for entry in elsewhere]

    # A trailing newline, so appending this to a longer note does not run the
    # last line into whatever follows it.
    return "\n".join(lines) + "\n"


def _where_it_stands(card: TaskDay) -> str:
    """What to put after a heading to say where this piece of work stands.

    Three answers, and the middle one is why this is a function: a card names
    its column, a sub-task names the card it belongs to, and something with
    neither has been deleted. Read straight off the column, an absent one would
    make every sub-task in the note look deleted.
    """
    if card.column:
        return f" ({card.column})"
    if card.parent:
        return f" (of {card.parent})"
    return " (deleted)"


def _bullet(entry: HistoryEntry, zone: ZoneInfo) -> str:
    """One event as a bullet: when, what, and who if it was not a person.

    An agent's work is named because the report is read as an account of a
    person's day, and an entry a bot wrote is not part of it.
    """
    when = entry.occurred_at.astimezone(zone).strftime("%H:%M")
    if entry.channel == "api":
        return f"- {when} · {entry.summary} — {entry.actor_label} (agent)"
    return f"- {when} · {entry.summary}"


def _todo_line(todo: DayTodo) -> str:
    """One to-do as a Markdown task-list item, saying what became of it."""
    box = "- [x]" if todo.state is TodoState.DONE else "- [ ]"
    line = f"{box} {todo.text}"
    if todo.state is TodoState.MOVED and todo.moved_to:
        line += f" → moved to {_short_date(todo.moved_to)}"
    elif todo.state is TodoState.NOT_DONE:
        line += " — not done"
    if todo.carried_from:
        line += f" (carried from {todo.carried_from:%a})"
    return line


def _short_date(day: date) -> str:
    """``Mon 19 Oct``."""
    return f"{day:%a} {day.day} {day:%b}"


def _long_date(day: date) -> str:
    """``Wednesday 2 September 2026``, without a platform-specific format code."""
    return f"{day:%A} {day.day} {day:%B %Y}"


def _plural(count: int, word: str) -> str:
    return word if count == 1 else f"{word}s"
