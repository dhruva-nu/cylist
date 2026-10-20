package cylist.app.push

/** What the pill's icon, left of the camera, shows. */
sealed interface PillMark {
    /** One agent at work: the robot. */
    data object Robot : PillMark

    /** Several: the robot with how many in its face, "9+" past nine. */
    data class Count(val label: String) : PillMark

    /** The one in the pill is asking you something: a question bubble. */
    data object Asks : PillMark
}

/**
 * Which live session holds the camera pill, and what its icon shows.
 *
 * OxygenOS shows one Live Update in the pill and draws no text left of the
 * camera, only the icon. So one session's notification asks to be promoted and
 * carries the count in its icon, while its short text names its own card on
 * the right. The others stay in the shade, unpromoted. A session asking you
 * something takes the pill over one that is only working, and among equals the
 * newest change does. Decided without Android so it can be tested: the
 * platform half is [AgentNotifier].
 */
data class AgentPill(val leader: String?, val mark: PillMark) {
    /** What [sessionId]'s notification is for this pill; it is redrawn when this changes. */
    fun roleOf(sessionId: String): String = when {
        sessionId != leader -> "shade"
        mark is PillMark.Count -> "count:${mark.label}"
        mark is PillMark.Asks -> "asks"
        else -> "robot"
    }

    companion object {
        /** The pill for [live]: the sessions still working or waiting, one update each. */
        fun of(live: Collection<AgentUpdate>): AgentPill {
            val asking = live.filter { AgentAlert.of(it).channel == AlertChannel.NeedsYou }
            val lead = asking.ifEmpty { live }.maxByOrNull { it.changedAtMillis }
                ?: return AgentPill(null, PillMark.Robot)
            val mark = when {
                lead in asking -> PillMark.Asks
                live.size == 1 -> PillMark.Robot
                live.size <= 9 -> PillMark.Count(live.size.toString())
                else -> PillMark.Count("9+")
            }
            return AgentPill(lead.sessionId, mark)
        }
    }
}
