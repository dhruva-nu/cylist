package cylist.app.board

import cylist.api.generated.models.AgentPresence
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TaskStatus
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.UUID
import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.sign

/*
 * What a board card says, decided apart from how it is drawn, so the rules can
 * be tested without a screen. Each one is the web app's, ported: the phone and
 * the browser should never disagree about whether a card is late or who it is
 * waiting on. The TypeScript each mirrors is named beside it.
 */

/** How near a due date is (`components/dates.ts`, `dueBucket`). */
enum class DueKind { Late, Today, Tomorrow, Soon, Later }

data class Due(val kind: DueKind, val text: String, val said: String)

private val dayMonth = DateTimeFormatter.ofPattern("d MMM", Locale.UK)

/**
 * The date a card is being asked for now, said as the card says it, or null
 * for a card that owes nothing (`board/CardFace.tsx`, `owedDateOf`). A
 * cancelled or finished card has nothing left to be late for.
 */
fun dueOf(task: TaskSummary, today: LocalDate): Due? = dueOf(task.status, task.finishedAt, task.nextDueDate, today)

/** [dueOf] from the three fields it reads, so a card's detail (a `TaskDetail`) says it the same way. */
fun dueOf(status: TaskStatus, finishedAt: String?, nextDueDate: String?, today: LocalDate): Due? {
    if (status == TaskStatus.cancelled || finishedAt != null) return null
    val owed = nextDueDate?.let { runCatching { LocalDate.parse(it.take(10)) }.getOrNull() } ?: return null
    val days = ChronoUnit.DAYS.between(today, owed).toInt()
    val date = owed.format(dayMonth)
    return when {
        days < 0 -> Due(DueKind.Late, "${-days}d late", "${-days} days late")
        days == 0 -> Due(DueKind.Today, "Today", "Due today")
        days == 1 -> Due(DueKind.Tomorrow, "Tomorrow", "Due tomorrow")
        days <= 7 -> Due(DueKind.Soon, date, "Due $date")
        else -> Due(DueKind.Later, date, "Due $date")
    }
}

/** The line under a card's title while an agent is on it. */
data class AgentLine(val waiting: Boolean, val label: String, val detail: String?)

/**
 * The agent line, or null when no agent is on the card now
 * (`routes/agentState.ts`, `agentIndicator`). A finished or disconnected
 * session draws nothing on the phone: the line is there only while an agent is.
 */
fun agentLineOf(presence: AgentPresence?, viewerId: UUID?): AgentLine? {
    if (presence == null) return null
    val many = presence.count > 1
    val agents = if (many) "${presence.count} agents" else "Agent"
    return when (presence.state) {
        AgentPresence.State.waiting -> {
            val asking = presence.prompt?.takeIf { it.status == AgentPromptStatus.`open` }?.let { promptLine(it, viewerId) }
            when {
                asking != null && many -> AgentLine(true, "$agents · one needs you", asking)
                asking != null -> AgentLine(true, asking, null)
                else -> AgentLine(true, if (many) "$agents · one needs you" else "Agent needs you", waitingDetail(presence.reason))
            }
        }
        AgentPresence.State.working -> {
            val answered = presence.prompt?.takeIf { it.status == AgentPromptStatus.answered }?.let { promptLine(it, viewerId) }
            val note = presence.note?.takeIf { it.isNotBlank() }
            when {
                answered != null && many -> AgentLine(false, "$agents working", answered)
                answered != null -> AgentLine(false, answered, note)
                note != null && many -> AgentLine(false, "$agents working", note)
                note != null -> AgentLine(false, note, null)
                else -> AgentLine(false, "$agents working", null)
            }
        }
        AgentPresence.State.done -> null
    }
}

private fun waitingDetail(reason: AgentSessionReason?): String = when (reason) {
    AgentSessionReason.permission -> "permission"
    AgentSessionReason.idle -> "idle"
    AgentSessionReason.question -> "a question"
    else -> "waiting for your reply"
}

/** One prompt as a line on a card or in the agent panel (`routes/agentState.ts`, `promptLine`). */
fun promptLine(prompt: AgentPromptRead, viewerId: UUID?): String? = when (prompt.status) {
    AgentPromptStatus.`open` -> if (prompt.kind == AgentPromptKind.question) "Asks: ${prompt.text}" else "Permission: ${prompt.text}"
    AgentPromptStatus.answered -> {
        val who = if (viewerId != null && prompt.answeredById == viewerId) "You" else prompt.answeredBy ?: "Answered"
        "$who: ${prompt.answer.orEmpty()}"
    }
    AgentPromptStatus.expired -> "Nobody answered in time"
    AgentPromptStatus.withdrawn -> if (prompt.closedReason == "answered_in_terminal") "Answered in the terminal" else null
    else -> null
}

/**
 * The PR state a card shows: the one furthest behind, since a card with one
 * merged and one still raised is not finished being reviewed.
 */
fun prStateOf(task: TaskSummary): PrState? {
    val states = task.prRefs.map { it.state ?: PrState.raised }
    return listOf(PrState.raised, PrState.approved, PrState.merged).firstOrNull { it in states }
}

/** Everybody a card belongs to: its assignee and whoever owns one of its sub-tasks (`boardPeople.ts`). */
fun ownersOf(task: TaskSummary): List<UUID> = listOf(task.assignee.id) + task.subtaskAssignees.map { it.id }

fun ownedByAny(task: TaskSummary, people: Set<UUID>): Boolean =
    people.isEmpty() || ownersOf(task).any { it in people }

/** How many cards each person owns, counting a card once however many of its sub-tasks they hold. */
fun cardsOwned(tasks: List<TaskSummary>): Map<UUID, Int> =
    tasks.flatMap { ownersOf(it).toSet() }.groupingBy { it }.eachCount()

/** The people filter's faces: you first, then by cards owned, then by name. */
fun <P> facesInOrder(people: List<P>, id: (P) -> UUID, name: (P) -> String, owned: Map<UUID, Int>, meId: UUID?): List<P> =
    people.sortedWith(
        compareByDescending<P> { id(it) == meId }
            .thenByDescending { owned[id(it)] ?: 0 }
            .thenBy { name(it) },
    )

/**
 * The cards the board draws at all. Sub-tasks have no column; cancelled cards
 * are hidden board-wide, as on the web (`boardHiding.ts`); archived ones are
 * in cold storage.
 */
fun onBoard(task: TaskSummary): Boolean =
    task.parentId == null && task.columnId != null && task.archivedAt == null && task.status != TaskStatus.cancelled

/** Cards on hold are folded into a line at their column's foot rather than listed. */
fun isHeld(task: TaskSummary): Boolean = task.status == TaskStatus.hold

/** Which agent cards to show: everything, only those working, or only those waiting on someone. */
enum class AgentFilter { All, Working, NeedsYou }

fun passesAgentFilter(task: TaskSummary, filter: AgentFilter): Boolean = when (filter) {
    AgentFilter.All -> true
    AgentFilter.Working -> task.agentSession?.state == AgentPresence.State.working
    AgentFilter.NeedsYou -> task.agentSession?.state == AgentPresence.State.waiting
}

/** "Dhruva N" → "DN": the two letters on an avatar. */
fun initialsOf(name: String): String =
    name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.take(2).joinToString("") { it.first().uppercase() }
        .ifEmpty { "?" }

/*
 * Swiping the card list sideways, between columns. The numbers are the
 * design's (the Board artboard on the mobile canvas); everything is in dp, and
 * velocity in dp per millisecond, so the rule reads the same on any screen.
 */

/** Movement before a gesture is given an axis. */
const val SWIPE_LOCK_DP = 8f

/** A gesture is sideways only when it is this much more sideways than up or down. */
const val SWIPE_AXIS_RATIO = 1.2f

/** A drag this far moves to the next column whatever its speed. */
const val SWIPE_COMMIT_DP = 72f

/** A flick: shorter than [SWIPE_COMMIT_DP], but at least this far and faster than [SWIPE_FLICK_DP_PER_MS]. */
const val SWIPE_FLICK_DP = 28f
const val SWIPE_FLICK_DP_PER_MS = 0.5f

/** At the first or last column the list moves this fraction of the drag, and springs back. */
const val SWIPE_RUBBER_BAND = 0.25f

enum class SwipeAxis { Sideways, UpDown }

/**
 * Who owns a gesture that has moved ([dx], [dy]) dp: null until it has moved
 * [SWIPE_LOCK_DP], then sideways only if clearly so. Anything else is the
 * list's, so the cards still scroll.
 */
fun swipeAxisOf(dx: Float, dy: Float): SwipeAxis? = when {
    hypot(dx, dy) < SWIPE_LOCK_DP -> null
    abs(dx) > SWIPE_AXIS_RATIO * abs(dy) -> SwipeAxis.Sideways
    else -> SwipeAxis.UpDown
}

/**
 * Where a sideways drag of [dx] dp, let go at [velocity] dp/ms, takes the
 * list: -1 to the previous column, +1 to the next, 0 back where it was. A
 * drag to the right (positive) brings the previous column in.
 */
fun swipeStep(dx: Float, velocity: Float, hasPrevious: Boolean, hasNext: Boolean): Int {
    val step = when {
        dx > 0 && hasPrevious -> -1
        dx < 0 && hasNext -> 1
        else -> return 0
    }
    val far = abs(dx) >= SWIPE_COMMIT_DP
    val flick = abs(dx) > SWIPE_FLICK_DP && velocity * sign(dx) > SWIPE_FLICK_DP_PER_MS
    return if (far || flick) step else 0
}

/** How far the list is drawn for a drag of [dx]: the whole drag, or a rubber band where there is no column to go to. */
fun swipeFollow(dx: Float, hasPrevious: Boolean, hasNext: Boolean): Float =
    if ((dx > 0 && hasPrevious) || (dx < 0 && hasNext)) dx else dx * SWIPE_RUBBER_BAND

/** The name a card gives its assignee: "You" for the viewer, otherwise the first name ("Aditi K" → "Aditi"). */
fun assigneeLabel(id: UUID, name: String, viewerId: UUID?): String =
    if (viewerId != null && id == viewerId) "You" else name.trim().split(Regex("\\s+")).first().ifEmpty { name }

/**
 * How many open cards each person is the assignee of: cards on the board and
 * not yet finished, which is what the assign sheet orders by.
 */
fun openCardsOf(tasks: List<TaskSummary>, lastColumnId: UUID?): Map<UUID, Int> =
    tasks.filter { onBoard(it) && it.finishedAt == null && it.columnId != lastColumnId }
        .groupingBy { it.assignee.id }.eachCount()

/** "1 open card", "3 open cards". */
fun openCardsText(count: Int): String = if (count == 1) "1 open card" else "$count open cards"

/**
 * The snackbar after a card is given to someone. When the people filter now
 * hides the card it says so, or the card would look lost.
 */
fun reassignedText(ref: String, name: String, toViewer: Boolean, hidden: Boolean): String {
    val whose = if (toViewer) "$ref is now yours." else "$ref is now $name's."
    return if (hidden) "$whose Your filter hides it." else whose
}
