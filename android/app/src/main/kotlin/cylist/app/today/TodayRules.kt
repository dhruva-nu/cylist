package cylist.app.today

import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.PickRead
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TodoRead
import cylist.api.generated.models.TodoState
import java.time.DayOfWeek
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.UUID

/*
 * What the Today screen says, decided apart from how it is drawn. Each rule is
 * the web app's, ported, so a day planned on the phone reads the same in the
 * browser: which cards are offered and in what order (`components/today.ts`),
 * how a date is said (`components/dates.ts`), and how a to-do and a wrap-up are
 * worded (`components/Today.tsx`). The TypeScript each mirrors is named beside it.
 *
 * Dates are `LocalDate`s in the phone's own zone, which is what `localToday`
 * cuts the day in on the web; the zone goes to the server with every call.
 */

// --- dates.ts -------------------------------------------------------------

private val dayMonth = DateTimeFormatter.ofPattern("d MMM", Locale.UK)
private val weekdayDayMonth = DateTimeFormatter.ofPattern("EEE d MMM", Locale.UK)
private val weekdayDayMonthLong = DateTimeFormatter.ofPattern("EEEE d MMMM", Locale.UK)
private val weekdayAbbrev = DateTimeFormatter.ofPattern("EEE", Locale.UK)
private val weekdayFull = DateTimeFormatter.ofPattern("EEEE", Locale.UK)

/** `13 Oct` (`formatDue`). */
fun formatDue(day: LocalDate): String = day.format(dayMonth)

/** `Mon 19 Oct` (`dayShort`). */
fun dayShort(day: LocalDate): String = day.format(weekdayDayMonth)

/** `Monday 19 October` (`dayLong`). */
fun dayLong(day: LocalDate): String = day.format(weekdayDayMonthLong)

/** `Mon` (`weekdayShort`). */
fun weekdayShort(day: LocalDate): String = day.format(weekdayAbbrev)

/** `Monday` (`weekdayName` in Today.tsx). */
fun weekdayName(day: LocalDate): String = day.format(weekdayFull)

/** The next weekday after this one: Friday's is Monday (`nextWorkingDay`). */
fun nextWorkingDay(day: LocalDate): LocalDate {
    var next = day.plusDays(1)
    while (next.dayOfWeek == DayOfWeek.SATURDAY || next.dayOfWeek == DayOfWeek.SUNDAY) next = next.plusDays(1)
    return next
}

/** A `YYYY-MM-DD` (or a timestamp starting with one) from the API, or null. */
fun apiDate(value: String?): LocalDate? = value?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() }

// --- today.ts -------------------------------------------------------------

/** Most urgent first, then the order the cards were numbered (`byPriority`). */
private val byPriority: Comparator<TaskSummary> =
    compareBy<TaskSummary> { priorityRank(it.priority) }.thenBy { it.number ?: 0 }.thenBy { it.reference }

private fun priorityRank(priority: TaskPriority): Int = when (priority) {
    TaskPriority.p0 -> 0
    TaskPriority.p1 -> 1
    TaskPriority.p2 -> 2
    TaskPriority.p3 -> 3
    else -> 3
}

/** Today's work off one board (`TodayWork`). */
data class TodayWork(val overdue: List<TaskSummary>, val due: List<TaskSummary>)

/**
 * The cards wanted by today (`todaysWork`): not in the last column, not
 * cancelled or on hold, yours when [me] is known, with a next due date on or
 * before [today]. Overdue is oldest first, then by priority.
 */
fun todaysWork(tasks: List<TaskSummary>, columns: List<ColumnRead>, me: UUID?, today: LocalDate): TodayWork {
    val doneColumn = columns.lastOrNull()?.id
    val wanted = tasks.filter { task ->
        val due = apiDate(task.nextDueDate)
        task.status != TaskStatus.cancelled && task.status != TaskStatus.hold &&
            (doneColumn == null || task.columnId != doneColumn) &&
            (me == null || task.assignee.id == me) &&
            due != null && !due.isAfter(today)
    }
    return TodayWork(
        overdue = wanted.filter { apiDate(it.nextDueDate)!!.isBefore(today) }
            .sortedWith(compareBy<TaskSummary> { apiDate(it.nextDueDate) }.then(byPriority)),
        due = wanted.filter { apiDate(it.nextDueDate) == today }.sortedWith(byPriority),
    )
}

/** One heading's worth of cards a day can be planned from (`CardGroup`). */
data class CardGroup(val name: String, val late: Boolean, val tasks: List<TaskSummary>)

/**
 * Every open card on a board as the planner offers them (`openCardGroups`):
 * not in the last column, not cancelled, not in cold storage. "Due or late"
 * first, then each other column nearest to done first, in board order.
 * [mine] narrows to your own cards and [query] to reference or title.
 */
fun openCardGroups(
    tasks: List<TaskSummary>,
    columns: List<ColumnRead>,
    me: UUID?,
    mine: Boolean,
    query: String,
    today: LocalDate,
): List<CardGroup> {
    val doneColumn = columns.lastOrNull()?.id
    val wanted = query.trim().lowercase()
    val open = tasks.filter { task ->
        task.columnId != null && task.columnId != doneColumn &&
            task.status != TaskStatus.cancelled &&
            !(mine && me != null && task.assignee.id != me) &&
            (wanted.isEmpty() || wanted in "${task.reference} ${task.title}".lowercase())
    }
    val dueOrLate = open
        .filter { apiDate(it.nextDueDate)?.let { due -> !due.isAfter(today) } == true }
        .sortedWith(compareBy<TaskSummary> { apiDate(it.nextDueDate) }.then(byPriority))
    val pulledUp = dueOrLate.map { it.id }.toSet()

    val groups = mutableListOf<CardGroup>()
    if (dueOrLate.isNotEmpty()) groups += CardGroup("Due or late", true, dueOrLate)
    for (column in columns.reversed()) {
        if (column.id == doneColumn) continue
        val here = open.filter { it.columnId == column.id && it.id !in pulledUp }.sortedBy { it.position ?: 0 }
        if (here.isNotEmpty()) groups += CardGroup(column.name, false, here)
    }
    return groups
}

/** How many cards [openCardGroups] offers, for the All / Mine counts. */
fun cardCount(groups: List<CardGroup>): Int = groups.sumOf { it.tasks.size }

/**
 * The first card of yours that is late and not on the day, if any: what turns
 * the Today count red on the web (`lateAndUnplanned`), and what the phone's
 * nudge names.
 */
fun lateAndUnplanned(tasks: List<TaskSummary>, columns: List<ColumnRead>, me: UUID?, today: LocalDate, planned: Set<UUID>): TaskSummary? =
    todaysWork(tasks, columns, me, today).overdue.firstOrNull { it.id !in planned }

/** "HWL-3 is 3 days late", for the nudge above the plan. */
fun lateSaid(task: TaskSummary, today: LocalDate): String {
    val days = apiDate(task.nextDueDate)?.let { ChronoUnit.DAYS.between(it, today).toInt() } ?: 0
    return "${task.reference} is ${if (days == 1) "a day" else "$days days"} late"
}

// --- Today.tsx ------------------------------------------------------------

enum class DueTone { Late, Today, Plain }

data class PickDue(val text: String, val tone: DueTone)

/** The due mark on a card in the add-cards sheet (`CardRow`): `13 Oct, late`, `Today` or `23 Oct`. */
fun pickDueOf(task: TaskSummary, today: LocalDate): PickDue? {
    val due = apiDate(task.nextDueDate) ?: return null
    return when {
        due.isBefore(today) -> PickDue("${formatDue(due)}, late", DueTone.Late)
        due == today -> PickDue("Today", DueTone.Today)
        else -> PickDue(formatDue(due), DueTone.Plain)
    }
}

/**
 * The marks after a to-do's words (`TodoRow`): where it came from, where it
 * went, or that it was left. The web puts what a card to-do does in a tooltip,
 * which a phone has not got, so a to-do from the card says which kind it is
 * when nothing else is said, as the design does.
 */
fun todoMarks(todo: TodoRead): List<String> {
    val marks = mutableListOf<String>()
    apiDate(todo.carriedFrom)?.let { marks += "from ${weekdayShort(it)}" }
    if (todo.state == TodoState.moved) apiDate(todo.movedTo)?.let { marks += "→ ${dayShort(it)}" }
    if (todo.state == TodoState.not_done) marks += "not done"
    if (marks.isEmpty()) {
        when (todo.sourceKind) {
            TodoRead.SourceKind.stage -> marks += "stage"
            TodoRead.SourceKind.subtask -> marks += "sub-task"
            TodoRead.SourceKind.checklist -> marks += "on the card"
            else -> Unit
        }
    }
    return marks
}

/** What ticking a to-do from the card does to the card (`ON_THE_CARD`), said under it on the phone. */
fun onTheCard(todo: TodoRead): String? = when (todo.sourceKind) {
    TodoRead.SourceKind.stage -> "Ticking this moves the card on to its next stage"
    TodoRead.SourceKind.subtask -> "Ticking this finishes the sub-task on the card"
    TodoRead.SourceKind.checklist -> "Ticking this ticks the box on the card"
    else -> null
}

/** `3 to-dos`, `1 to-do`. */
fun todos(count: Int): String = "$count ${if (count == 1) "to-do" else "to-dos"}"

/** `2 cards, 5 to-dos`, beside "Plan for today". */
fun planNote(picks: List<PickRead>): String? {
    if (picks.isEmpty()) return null
    val total = picks.sumOf { it.todos.size }
    return "${picks.size} ${if (picks.size == 1) "card" else "cards"}, ${todos(total)}"
}

/** "Plan for today" or "Plan for Friday 16 October". */
fun planTitle(day: LocalDate, today: LocalDate): String = if (day == today) "Plan for today" else "Plan for ${dayLong(day)}"

/** The open to-dos a wrap-up deals with, by card, in plan order (`WrapUpDialog`'s `open`). */
fun openByCard(picks: List<PickRead>): List<Pair<PickRead, List<TodoRead>>> =
    picks.map { pick -> pick to pick.todos.filter { it.state == TodoState.`open` } }.filter { it.second.isNotEmpty() }

/** Which day a wrap-up moves to-dos to. */
enum class WrapTarget { NextWorkingDay, AnotherDay }

/**
 * Everything the wrap-up sheet says and sends, from the to-dos still open and
 * those unticked. Every open to-do starts ticked, as on the web; ticked ones
 * move to [toDay] and the rest stay on [day] as not done.
 */
data class WrapUpPlan(val day: LocalDate, val toDay: LocalDate, val open: List<UUID>, val kept: Set<UUID>) {
    val moving: List<UUID> get() = open.filter { it !in kept }
    val staying: Int get() = open.size - moving.size
    val allTicked: Boolean get() = open.isNotEmpty() && moving.size == open.size

    /** "3 to-dos are still open. Ticked ones move on; the rest stay on Friday as not done." */
    val lead: String get() =
        "${todos(open.size)} ${if (open.size == 1) "is" else "are"} still open. " +
            "Ticked ones move on; the rest stay on ${weekdayName(day)} as not done."

    /** The confirm button: `Move 3 to Mon`, or `Leave all as not done` when nothing is ticked. */
    val confirm: String get() = if (moving.isEmpty()) "Leave all as not done" else "Move ${moving.size} to ${weekdayShort(toDay)}"

    /** A ticked row's mark, `→ Mon`; an unticked one's is "stays". */
    val movingMark: String get() = "→ ${weekdayShort(toDay)}"

    /** Another day must be after the day being closed; the server refuses otherwise. */
    val canConfirm: Boolean get() = toDay.isAfter(day)
}

/** The day a wrap-up targets (`WrapUpDialog`'s `toDay`). */
fun wrapTargetDay(day: LocalDate, target: WrapTarget, otherDay: LocalDate): LocalDate = when (target) {
    WrapTarget.NextWorkingDay -> nextWorkingDay(day)
    WrapTarget.AnotherDay -> otherDay
}

/** The first "another day" offered: the day after the next working day (`WrapUpDialog`). */
fun firstOtherDay(day: LocalDate): LocalDate = nextWorkingDay(day).plusDays(1)

/**
 * What the result sheet says after the server answered (`WrapUpDialog`'s result):
 * "2 to-dos moved to Monday 19 October. 1 to-do stayed on Friday as not done."
 */
fun wrapResultText(day: LocalDate, toDay: LocalDate, moved: Int, notDone: Int): String {
    val first = if (moved > 0) "${todos(moved)} moved to ${dayLong(toDay)}." else "Nothing moved on."
    val second = if (notDone > 0) " ${todos(notDone)} stayed on ${weekdayName(day)} as not done." else ""
    return first + second
}
