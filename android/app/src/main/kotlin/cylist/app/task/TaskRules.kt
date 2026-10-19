package cylist.app.task

import cylist.api.generated.models.AgentFeedKind
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.HistoryEntry
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TemplateRead
import java.net.URI
import java.net.URISyntaxException
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale
import java.util.UUID

/*
 * What the Task detail screen says and allows, decided apart from how it is
 * drawn, so the rules can be tested without a screen. Each one is the web
 * app's (or, for a move, the server's), ported: the phone and the browser
 * should never disagree about whether a card may go to Done or who answered
 * an agent. The TypeScript or Python each mirrors is named beside it.
 */

/** An ISO timestamp from the server as an instant, or null if it is not one. */
fun instantOf(iso: String?): Instant? = iso?.let {
    try {
        OffsetDateTime.parse(it).toInstant()
    } catch (_: DateTimeParseException) {
        null
    }
}

// --- Jira and pull-request rows (components/primitives/TaskRef.tsx) ----------

/** A stored reference as it is shown, and where it points: null when it is not a URL. */
data class RefDisplay(val label: String, val href: String?)

/** `https://…` only: what keeps a `javascript:` value typed into a box out of a link. */
private fun asUrl(value: String): URI? {
    if (!Regex("^https?://", RegexOption.IGNORE_CASE).containsMatchIn(value)) return null
    return try {
        URI(value).takeIf { it.host != null }
    } catch (_: URISyntaxException) {
        null
    }
}

private fun lastSegment(url: URI): String = url.path.orEmpty().split('/').lastOrNull { it.isNotEmpty() } ?: url.host

/** `jiraRefDisplay`: a pasted URL reads as the key inside it, `ATL-41`. */
fun jiraRefDisplay(value: String): RefDisplay {
    val url = asUrl(value) ?: return RefDisplay(value, null)
    val key = Regex("[A-Z][A-Z0-9]*-\\d+", RegexOption.IGNORE_CASE).find("${url.path.orEmpty()} ${url.query.orEmpty()}")
    return RefDisplay(key?.value?.uppercase() ?: lastSegment(url), value)
}

/** `prRefDisplay`: a pull request's URL reads as `#212`. */
fun prRefDisplay(value: String): RefDisplay {
    val url = asUrl(value) ?: return RefDisplay(value, null)
    val number = Regex("/(?:pull|pull-requests|merge_requests)/(\\d+)", RegexOption.IGNORE_CASE).find(url.path.orEmpty())
    return RefDisplay(number?.let { "#${it.groupValues[1]}" } ?: lastSegment(url), value)
}

/** `prRefRepo`: the repository a pull request is in, `cylist`, or null when the value does not say. */
fun prRepoOf(value: String): String? {
    val url = asUrl(value) ?: return null
    val segments = url.path.orEmpty().split('/').filter { it.isNotEmpty() && it != "-" }
    val at = segments.indexOfFirst { Regex("^(?:pull|pulls|pull-requests|merge_requests)$", RegexOption.IGNORE_CASE).matches(it) }
    return if (at > 0) segments[at - 1] else null
}

// --- The agent feed (components/agentFeed.ts) --------------------------------

/** The word in front of a feed line. */
fun feedLabel(kind: AgentFeedKind): String = when (kind) {
    AgentFeedKind.said -> "Said"
    AgentFeedKind.read -> "Read"
    AgentFeedKind.edit -> "Edit"
    AgentFeedKind.run -> "Ran"
    AgentFeedKind.output -> "Got"
    AgentFeedKind.error -> "Failed"
    AgentFeedKind.tool -> "Tool"
    else -> "Did"
}

/** Whether a line is code-shaped (a path, a command, its output) and set in mono. */
fun isCodeLike(kind: AgentFeedKind): Boolean = kind != AgentFeedKind.said

private val hourMinute = DateTimeFormatter.ofPattern("HH:mm", Locale.UK)
private val dayMonthTime = DateTimeFormatter.ofPattern("d MMM, HH:mm", Locale.UK)

/** `14:31` in the reader's zone: a feed is read the day it is written. */
fun feedClock(at: String, zone: ZoneId): String = instantOf(at)?.atZone(zone)?.format(hourMinute).orEmpty()

/** "14:29" today, "11 Oct, 14:29" on any other day (`AgentPanel.tsx`, `clock`). */
fun clock(at: String, now: Instant, zone: ZoneId): String {
    val time = instantOf(at)?.atZone(zone) ?: return ""
    return if (time.toLocalDate() == now.atZone(zone).toLocalDate()) time.format(hourMinute) else time.format(dayMonthTime)
}

// --- Sessions (routes/agentState.ts) -----------------------------------------

/** How long a session has been quiet, said the way a person would (`silentFor`). */
fun silentFor(lastSeenAt: String, now: Instant): String {
    val minutes = (instantOf(lastSeenAt)?.let { Duration.between(it, now).toMinutes() } ?: 0).coerceAtLeast(0)
    return when {
        minutes < 60 -> "${minutes}m"
        minutes < 60 * 24 -> "${minutes / 60}h"
        else -> "${minutes / (60 * 24)}d"
    }
}

/** What an open session is doing this moment, or null once it has stopped (`nowLine`). */
fun nowLine(session: AgentSessionRead): String? {
    if (session.state == AgentSessionState.done) return null
    session.note?.takeIf { it.isNotEmpty() }?.let { return it }
    if (session.state == AgentSessionState.working) return "Working"
    return when (session.reason) {
        AgentSessionReason.permission -> "Waiting for permission"
        AgentSessionReason.question -> "Waiting for an answer"
        AgentSessionReason.idle -> "Idle"
        else -> "Waiting for your reply"
    }
}

enum class SessionTone { Working, Waiting, Done, Stale }

/** A session's state in a word or four, its tone, and since when (`sessionPill`). */
data class SessionPill(val tone: SessionTone, val label: String, val sinceVerb: String, val sinceAt: String)

fun sessionPill(session: AgentSessionRead): SessionPill {
    session.endedAt?.let { ended ->
        return when (session.reason) {
            AgentSessionReason.moved -> SessionPill(SessionTone.Done, "Moved to another card", "ended", ended)
            AgentSessionReason.connection_lost -> SessionPill(SessionTone.Stale, "Disconnected", "ended", ended)
            else -> SessionPill(SessionTone.Done, "Finished", "ended", ended)
        }
    }
    if (session.state == AgentSessionState.working) {
        return SessionPill(SessionTone.Working, "Working", "working since", session.stateChangedAt)
    }
    val open = session.prompt?.takeIf { isAnswerable(it, null) }
    if (open != null) {
        val label = if (open.kind == AgentPromptKind.question) "Waiting: a question for you" else "Waiting: permission"
        return SessionPill(SessionTone.Waiting, label, "waiting since", open.askedAt)
    }
    val label = if (session.reason == AgentSessionReason.idle) "Waiting: idle" else "Waiting for your reply"
    return SessionPill(SessionTone.Waiting, label, "waiting since", session.stateChangedAt)
}

/**
 * Whether a person can still answer this (`isAnswerable`). With [now], a
 * prompt past its `expires_at` is not, even before the server has said so:
 * the server refuses an answer from that moment, and buttons that can only
 * fail are worse than none.
 */
fun isAnswerable(prompt: AgentPromptRead?, now: Instant?): Boolean {
    if (prompt == null || prompt.status != AgentPromptStatus.`open`) return false
    if (now == null) return true
    val expires = instantOf(prompt.expiresAt) ?: return true
    return expires.isAfter(now)
}

/** "You" to whoever answered, their name to anyone else, "Answered" for an answer that is nobody's (`answeredBy`). */
fun answeredBy(prompt: AgentPromptRead, viewerId: UUID?): String =
    if (viewerId != null && prompt.answeredById == viewerId) "You" else prompt.answeredBy ?: "Answered"

// --- Answering (components/promptAnswer.ts) ----------------------------------

/** "Question from the agent · 5m ago", "Permission asked · just now". */
fun promptHeading(prompt: AgentPromptRead, now: Instant): String {
    val what = if (prompt.kind == AgentPromptKind.question) "Question from the agent" else "Permission asked"
    val waited = silentFor(prompt.askedAt, now)
    return "$what · ${if (waited == "0m") "just now" else "$waited ago"}"
}

/**
 * A typed reply as an answer, or null when there is nothing to send: an empty
 * box, or a prompt that takes no reply (a permission is one of its choices).
 */
fun replyAnswer(prompt: AgentPromptRead, text: String): AgentPromptAnswer? {
    if (!prompt.allowsReply) return null
    val trimmed = text.trim()
    return if (trimmed.isEmpty()) null else AgentPromptAnswer(text = trimmed)
}

enum class ChoiceTone { Go, Plain, Danger }

/** A permission's "Allow once" is the green for yes and "Deny" the red for no; every other choice is plain. */
fun choiceTone(prompt: AgentPromptRead, key: String): ChoiceTone = when {
    prompt.kind != AgentPromptKind.permission -> ChoiceTone.Plain
    key == "allow_once" -> ChoiceTone.Go
    key == "deny" -> ChoiceTone.Danger
    else -> ChoiceTone.Plain
}

// --- Moving a card (services/tasks/moves.py, board/useBoardDrag.ts) ----------

/** What asking to move a card to a column comes to, before anything is sent. */
sealed interface MovePlan {
    /** Send it. */
    data object Go : MovePlan

    /** It is already there, on that outcome: nothing to send. */
    data object Stay : MovePlan

    /** Out of the last column: it stops being finished, which is asked first (`ReopenDialog.tsx`). */
    data class Reopen(val from: String, val to: String) : MovePlan

    /** Not allowed; [reason] says why in the server's words. */
    data class Refused(val reason: String) : MovePlan
}

/** A sub-task stops holding its parent back once finished or cancelled (`Task.is_settled`). */
fun isSettled(subtask: TaskRead): Boolean = subtask.status == TaskStatus.cancelled || subtask.finishedAt != null

/** What is still open under a card: its sub-tasks by reference, then its checklist items by title (`open_work`). */
fun outstandingWork(task: TaskDetail): List<String> =
    task.subtasks.filterNot(::isSettled).map { it.reference } +
        task.checklist.filter { it.state == ChecklistState.`open` }.map { it.title }

/** The outcomes a card may end on in [column]: none outside the board's last column (`permitted_outcomes`). */
fun outcomesFor(task: TaskDetail, column: ColumnRead, columns: List<ColumnRead>, templates: List<TemplateRead>): List<String> {
    if (column.id != columns.lastOrNull()?.id || column.outcomes.isEmpty()) return emptyList()
    val stage = templates.firstOrNull { it.id == task.templateId }?.stages?.firstOrNull { it.columnId == column.id }
    if (stage == null || stage.allowedOutcomes.isEmpty()) return column.outcomes
    val allowed = stage.allowedOutcomes.map { it.lowercase() }.toSet()
    return column.outcomes.filter { it.lowercase() in allowed }.ifEmpty { column.outcomes }
}

/**
 * Whether moving [task] to [targetId], ending on [outcome] where the column
 * has outcomes, may be sent, needs a yes first, or is refused.
 *
 * The server has the last word on all of it; this says out loud what the
 * phone already knows, as the web board greys a column a card's template
 * keeps it out of (`forbiddenColumnsFor`) and the server refuses a card with
 * open work under it reaching the last column (`refuse_open_work`).
 */
fun planMove(
    task: TaskDetail,
    columns: List<ColumnRead>,
    templates: List<TemplateRead>,
    targetId: UUID,
    outcome: String?,
): MovePlan {
    val target = columns.firstOrNull { it.id == targetId } ?: return MovePlan.Refused("That column is not on this board any more.")
    val last = columns.last()
    if (task.parentId != null) {
        return MovePlan.Refused("${task.reference} is a sub-task, so it is not on the board. Finish it instead.")
    }
    if (task.archivedAt != null) {
        return MovePlan.Refused("${task.reference} is in cold storage. Restore it from the web app first.")
    }
    val template = templates.firstOrNull { it.id == task.templateId }
    if (template != null && template.allowedColumnIds.isNotEmpty() && targetId !in template.allowedColumnIds) {
        return MovePlan.Refused("${template.name} cards do not go to ${target.name.trim()}.")
    }
    val outcomes = outcomesFor(task, target, columns, templates)
    val sameOutcome = outcome == null || outcomes.isEmpty() || outcome.equals(task.outcome, ignoreCase = true)
    if (task.columnId == targetId) return if (sameOutcome) MovePlan.Stay else MovePlan.Go
    if (targetId == last.id) {
        val open = outstandingWork(task)
        if (open.isNotEmpty()) {
            val noun = if (open.size == 1) "sub-task" else "sub-tasks"
            return MovePlan.Refused(
                "${task.reference} still has ${open.size} unfinished $noun: ${open.joinToString(", ")}. " +
                    "Finish or cancel each of them before moving this card to ${last.name.trim()}.",
            )
        }
    }
    if (task.columnId == last.id) return MovePlan.Reopen(from = last.name.trim(), to = target.name.trim())
    return MovePlan.Go
}

// --- Ticking (taskDialog/useSubtaskTicking.ts) -------------------------------

/** What a tap on a checklist item makes it: open ⇄ done. A cancelled one is settled already; null. */
fun nextChecklistState(state: ChecklistState): ChecklistState? = when (state) {
    ChecklistState.`open` -> ChecklistState.done
    ChecklistState.done -> ChecklistState.`open`
    else -> null
}

// --- The rest of the card ----------------------------------------------------

/**
 * Who added the card: whoever its oldest history entry says created it. The
 * server keeps no reporter field; `task.created` is recorded with the actor,
 * which is the same person the web app's history names.
 */
fun reporterOf(oldest: HistoryEntry?): String? = oldest?.takeIf { it.verb == "task.created" }?.actorLabel

/** The column the date a card owes now belongs to: "Today, 16 Oct in Review". Null for the card's own due date. */
fun dueColumnOf(task: TaskDetail): String? {
    val owed = task.nextDueDate ?: return null
    if (owed == task.dueDate) return null
    return task.columnDueDates.firstOrNull { !it.met && it.dueDate == owed }?.columnName?.trim()
}

/**
 * Where each `@Name` of a project member sits in [text], to draw it as a tag.
 * A tag is the name as typed (`components/mentions.ts`): an `@` glued to a
 * word before it (`deploy@example.com`) names nobody, and nor does a name
 * running on into more word characters. The longest name wins where two start
 * alike ("Aditi" and "Aditi K").
 */
fun mentionRanges(text: String, names: List<String>): List<IntRange> {
    val sorted = names.filter { it.isNotBlank() }.sortedByDescending { it.length }
    val ranges = mutableListOf<IntRange>()
    var i = 0
    while (i < text.length) {
        if (text[i] == '@' && (i == 0 || !isWordChar(text[i - 1]))) {
            val name = sorted.firstOrNull { text.regionMatches(i + 1, it, 0, it.length, ignoreCase = true) && !isWordChar(text.getOrNull(i + 1 + it.length)) }
            if (name != null) {
                ranges += i..(i + name.length)
                i += name.length + 1
                continue
            }
        }
        i++
    }
    return ranges
}

private fun isWordChar(c: Char?): Boolean = c != null && (c.isLetterOrDigit() || c == '_')

private val dayMonthYear = DateTimeFormatter.ofPattern("d MMM yyyy", Locale.UK)

/** When a comment was written, as the design says it: "just now", "5 minutes ago", "2 days ago", then the date. */
fun ago(at: String, now: Instant, zone: ZoneId): String {
    val then = instantOf(at) ?: return ""
    val minutes = Duration.between(then, now).toMinutes().coerceAtLeast(0)
    fun said(n: Long, unit: String) = if (n == 1L) "1 $unit ago" else "$n ${unit}s ago"
    return when {
        minutes < 1 -> "just now"
        minutes < 60 -> said(minutes, "minute")
        minutes < 60 * 24 -> said(minutes / 60, "hour")
        minutes < 60 * 24 * 30 -> said(minutes / (60 * 24), "day")
        else -> then.atZone(zone).format(dayMonthYear)
    }
}

/** Where the card stands in its column's stages: "Review, 2 of 3", or null for a card with none. */
fun stageNote(task: TaskDetail): String? {
    val stages = task.subStatuses
    val index = task.subStatusIndex ?: return null
    val label = stages.getOrNull(index) ?: return null
    return "$label, ${index + 1} of ${stages.size}"
}

/** A `YYYY-MM-DD` day in full, "29 Aug 2026", as the web app's detail view says one; as given if it is not one. */
fun dayOf(iso: String): String = try {
    java.time.LocalDate.parse(iso.take(10)).format(dayMonthYear)
} catch (_: DateTimeParseException) {
    iso
}
