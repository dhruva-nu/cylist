package cylist.app.push

import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneOffset
import java.time.format.DateTimeParseException

/** What an agent session is doing, as the server reports it. */
enum class AgentState { Working, Waiting, Done }

/**
 * One agent session's new state, read from an FCM data message.
 *
 * The server sends these from `backend/app/services/push/messages.py`
 * (`data_for`): every value a string, an absent one empty. [parse] is the one
 * place that reads that shape, and it refuses anything it does not recognise
 * rather than drawing half a notification.
 */
data class AgentUpdate(
    /** The server's id for the session, which keys its one notification. */
    val sessionId: String,
    /** The card's reference, `ATL-41`. */
    val task: String,
    val taskTitle: String,
    val project: String,
    val state: AgentState,
    /** Why it waits or why it ended: `question`, `permission`, `turn_ended`, `session_ended`… */
    val reason: String?,
    /** The question, the permission asked, or what it is doing. */
    val note: String?,
    val clientName: String?,
    val startedAtMillis: Long,
    val changedAtMillis: Long,
) {
    /**
     * This update in the server's shape again, so a notification can carry it
     * and be redrawn later from what is showing: [parse] reads it back as is.
     */
    fun toData(): Map<String, String> = mapOf(
        "kind" to KIND,
        "session_id" to sessionId,
        "task" to task,
        "task_title" to taskTitle,
        "project" to project,
        "state" to state.name.lowercase(),
        "reason" to reason.orEmpty(),
        "note" to note.orEmpty(),
        "client_name" to clientName.orEmpty(),
        "started_at" to iso(startedAtMillis),
        "state_changed_at" to iso(changedAtMillis),
    )

    companion object {
        const val KIND = "agent_session"

        /** The update in [data], or null when it is not an agent-session push this app understands. */
        fun parse(data: Map<String, String>): AgentUpdate? {
            if (data["kind"] != KIND) return null
            val state = when (data["state"]) {
                "working" -> AgentState.Working
                "waiting" -> AgentState.Waiting
                "done" -> AgentState.Done
                else -> return null
            }
            val sessionId = data["session_id"]?.takeIf { it.isNotBlank() } ?: return null
            val task = data["task"]?.takeIf { it.isNotBlank() } ?: return null
            return AgentUpdate(
                sessionId = sessionId,
                task = task,
                taskTitle = data["task_title"].orEmpty(),
                project = data["project"].orEmpty(),
                state = state,
                reason = data["reason"]?.takeIf { it.isNotBlank() },
                note = data["note"]?.takeIf { it.isNotBlank() },
                clientName = data["client_name"]?.takeIf { it.isNotBlank() },
                startedAtMillis = millis(data["started_at"]) ?: return null,
                changedAtMillis = millis(data["state_changed_at"]) ?: return null,
            )
        }

        private fun iso(millis: Long): String = Instant.ofEpochMilli(millis).atOffset(ZoneOffset.UTC).toString()

        private fun millis(iso: String?): Long? = try {
            iso?.let { OffsetDateTime.parse(it).toInstant().toEpochMilli() }
        } catch (_: DateTimeParseException) {
            null
        }
    }
}
