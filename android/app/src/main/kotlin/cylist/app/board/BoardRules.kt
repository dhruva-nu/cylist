package cylist.app.board

import cylist.api.generated.models.AgentPresence
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import java.time.LocalDate
import java.time.format.DateTimeFormatter
import java.time.temporal.ChronoUnit
import java.util.Locale
import java.util.UUID

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
fun dueOf(task: TaskRead, today: LocalDate): Due? = dueOf(task.status, task.finishedAt, task.nextDueDate, today)

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
fun prStateOf(task: TaskRead): PrState? {
    val states = task.prRefs.map { it.state ?: PrState.raised }
    return listOf(PrState.raised, PrState.approved, PrState.merged).firstOrNull { it in states }
}

/** Everybody a card belongs to: its assignee and whoever owns one of its sub-tasks (`boardPeople.ts`). */
fun ownersOf(task: TaskRead): List<UUID> = listOf(task.assignee.id) + task.subtaskAssignees.map { it.id }

fun ownedByAny(task: TaskRead, people: Set<UUID>): Boolean =
    people.isEmpty() || ownersOf(task).any { it in people }

/** How many cards each person owns, counting a card once however many of its sub-tasks they hold. */
fun cardsOwned(tasks: List<TaskRead>): Map<UUID, Int> =
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
fun onBoard(task: TaskRead): Boolean =
    task.parentId == null && task.columnId != null && task.archivedAt == null && task.status != TaskStatus.cancelled

/** Cards on hold are folded into a line at their column's foot rather than listed. */
fun isHeld(task: TaskRead): Boolean = task.status == TaskStatus.hold

/** Which agent cards to show: everything, only those working, or only those waiting on someone. */
enum class AgentFilter { All, Working, NeedsYou }

fun passesAgentFilter(task: TaskRead, filter: AgentFilter): Boolean = when (filter) {
    AgentFilter.All -> true
    AgentFilter.Working -> task.agentSession?.state == AgentPresence.State.working
    AgentFilter.NeedsYou -> task.agentSession?.state == AgentPresence.State.waiting
}

/** "Dhruva N" → "DN": the two letters on an avatar. */
fun initialsOf(name: String): String =
    name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.take(2).joinToString("") { it.first().uppercase() }
        .ifEmpty { "?" }
