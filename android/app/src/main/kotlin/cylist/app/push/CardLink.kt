package cylist.app.push

/**
 * "Open this card's agent session": what tapping an agent notification asks
 * of the app. Carried on the notification's intent as a `cylist://card/<ref>`
 * URI with the session as a query parameter, so each card's tap is a distinct
 * intent rather than the last one overwriting the rest.
 */
data class CardLink(val task: String, val sessionId: String?) {
    fun toUri(): String = "$SCHEME://$HOST/$task" + (sessionId?.let { "?$SESSION=$it" } ?: "")

    companion object {
        const val SCHEME = "cylist"
        const val HOST = "card"
        const val SESSION = "session"

        private val REFERENCE = Regex("[A-Za-z][A-Za-z0-9]{0,9}-\\d+(-\\d+)?")
        private val SESSION_ID = Regex("[0-9a-fA-F-]{36}")

        /** The link in [uri], or null for anything else — an intent can come from anywhere. */
        fun parse(uri: String?): CardLink? {
            if (uri == null) return null
            val prefix = "$SCHEME://$HOST/"
            if (!uri.startsWith(prefix)) return null
            val rest = uri.removePrefix(prefix)
            val task = rest.substringBefore('?')
            if (!REFERENCE.matches(task)) return null
            val session = rest.substringAfter('?', "")
                .split('&')
                .firstOrNull { it.startsWith("$SESSION=") }
                ?.removePrefix("$SESSION=")
                ?.takeIf { SESSION_ID.matches(it) }
            return CardLink(task, session)
        }
    }
}
