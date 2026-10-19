package cylist.app.push

/**
 * The notification channels, as the design's "How these reach the phone"
 * note has them. A person can silence each on its own in the system settings.
 */
enum class AlertChannel(val id: String, val label: String, val description: String) {
    NeedsYou(
        "agent_needs_you",
        "An agent needs you",
        "A question or a permission it can't go on without. Rings, and stays until it is answered.",
    ),
    Running(
        "agent_running",
        "Running agents",
        "A Live Update while an agent works on one of your cards, with how long it has been at it.",
    ),
    Finished(
        "agent_finished",
        "An agent finished",
        "When an agent's session on one of your cards ends.",
    ),
}

/**
 * What one [AgentUpdate] looks like as a notification, decided without Android
 * so it can be tested: the platform half is [AgentNotifier].
 *
 * Working and waiting are one ongoing notification per session that asks to
 * be promoted, which is what makes it an Android 16 Live Update and what
 * OxygenOS shows in the camera pill. Each new state replaces the last in place.
 * Finished ends the Live Update with an ordinary notification.
 */
data class AgentAlert(
    val channel: AlertChannel,
    val title: String,
    val text: String,
    /** The status-bar chip's few characters, or null to show [chronometerSinceMillis] instead. */
    val chip: String?,
    val ongoing: Boolean,
    /** Ask Android 16 to show it as a Live Update. */
    val promoted: Boolean,
    /** Count up from here: how long the agent has been working. */
    val chronometerSinceMillis: Long?,
) {
    companion object {
        fun of(update: AgentUpdate): AgentAlert {
            val ref = update.task
            val card = update.taskTitle.ifBlank { ref }
            return when (update.state) {
                AgentState.Working -> AgentAlert(
                    channel = AlertChannel.Running,
                    title = "Working on $ref",
                    text = update.note ?: card,
                    chip = null,
                    ongoing = true,
                    promoted = true,
                    chronometerSinceMillis = update.startedAtMillis,
                )
                AgentState.Waiting -> waiting(update, ref, card)
                AgentState.Done -> AgentAlert(
                    channel = AlertChannel.Finished,
                    title = when (update.reason) {
                        "connection_lost" -> "Agent lost touch on $ref"
                        "moved" -> "Agent moved off $ref"
                        else -> "Agent finished $ref"
                    },
                    text = card,
                    chip = null,
                    ongoing = false,
                    promoted = false,
                    chronometerSinceMillis = null,
                )
            }
        }

        private fun waiting(update: AgentUpdate, ref: String, card: String): AgentAlert = when (update.reason) {
            "question" -> needsYou("Agent needs you on $ref", update.note ?: "It asked you a question.")
            "permission" -> needsYou("Agent wants permission on $ref", update.note ?: "A tool is waiting for your go-ahead.")
            // Its turn ended, or it has idled at the prompt: worth seeing, not worth ringing.
            else -> AgentAlert(
                channel = AlertChannel.Running,
                title = "Agent is waiting on $ref",
                text = update.note ?: "Waiting for your reply · $card",
                chip = "Your turn",
                ongoing = true,
                promoted = true,
                chronometerSinceMillis = null,
            )
        }

        private fun needsYou(title: String, text: String) = AgentAlert(
            channel = AlertChannel.NeedsYou,
            title = title,
            text = text,
            chip = "Needs you",
            ongoing = true,
            promoted = true,
            chronometerSinceMillis = null,
        )
    }
}
