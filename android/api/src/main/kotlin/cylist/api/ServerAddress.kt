package cylist.api

import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrlOrNull

/**
 * Where a Cylist server lives: its origin, plus a base path if it is served
 * under one. Never ends in a slash, and never in `/api/v1`, since every path
 * in the generated client already starts with that.
 *
 * Made only by [parse], so holding one means the address has been checked.
 */
@ConsistentCopyVisibility
data class ServerAddress private constructor(val url: String) {
    override fun toString(): String = url

    /** What to show for this server: the host, and the port and path when there are any. */
    val display: String
        get() = url.substringAfter("://")

    /** Why [parse] would not take some text. Each message is written to be shown as it is. */
    enum class Problem(val message: String) {
        Empty("Enter the server's address."),
        Malformed("That is not a web address."),
        UnsupportedScheme("Use an https:// address."),
        Cleartext("This build talks to servers over https:// only."),
        HasCredentials("Leave the user name and password out of the address."),
        HasQuery("Leave anything after ? or # out of the address."),
    }

    sealed interface Parsed {
        data class Valid(val address: ServerAddress) : Parsed
        data class Invalid(val problem: Problem) : Parsed
    }

    companion object {
        private const val API_PREFIX = "/api/v1"

        /**
         * Read what somebody typed into the server box.
         *
         * A bare host is taken as `https://`. Plain `http://` is refused unless
         * the host is one of [cleartextHosts], which a debug build sets to the
         * same hosts its network security config lets through (the emulator's
         * `10.0.2.2` and `localhost`), so the check here and Android's agree.
         * A pasted API URL (`…/api/v1`) is cut back to the server it belongs to.
         */
        fun parse(text: String, cleartextHosts: Set<String> = emptySet()): Parsed {
            val trimmed = text.trim()
            if (trimmed.isEmpty()) return Parsed.Invalid(Problem.Empty)

            val scheme = Regex("^([A-Za-z][A-Za-z0-9+.-]*)://").find(trimmed)?.groupValues?.get(1)?.lowercase()
            if (scheme != null && scheme != "https" && scheme != "http") {
                return Parsed.Invalid(Problem.UnsupportedScheme)
            }
            val url: HttpUrl = (if (scheme == null) "https://$trimmed" else trimmed).toHttpUrlOrNull()
                ?: return Parsed.Invalid(Problem.Malformed)

            if (url.username.isNotEmpty() || url.password.isNotEmpty()) return Parsed.Invalid(Problem.HasCredentials)
            if (url.query != null || url.fragment != null) return Parsed.Invalid(Problem.HasQuery)
            if (!url.isHttps && url.host !in cleartextHosts) return Parsed.Invalid(Problem.Cleartext)

            val path = url.encodedPath.trimEnd('/').removeSuffix(API_PREFIX).trimEnd('/')
            val origin = url.newBuilder().encodedPath("/").build().toString().trimEnd('/')
            return Parsed.Valid(ServerAddress(origin + path))
        }

        /**
         * An address [parse] accepted earlier and that was stored as its [url].
         * Not a second check: what this build allows may have changed since,
         * and the stored session is what it is. Null if it is not a URL at all.
         */
        fun restore(url: String): ServerAddress? = url.toHttpUrlOrNull()?.let { ServerAddress(url) }
    }
}
