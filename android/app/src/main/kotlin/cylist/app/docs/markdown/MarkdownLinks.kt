package cylist.app.docs.markdown

import java.net.URI
import java.net.URISyntaxException

/** Where a link in a doc goes when it is tapped. */
object MarkdownLinks {
    /**
     * The schemes react-markdown's `defaultUrlTransform` lets through. Any
     * other scheme (`javascript:`, `intent:`, `file:`) leaves the link inert on
     * the web, and so it does here.
     */
    private val SAFE_SCHEMES = setOf("http", "https", "mailto", "xmpp", "irc", "ircs")

    /**
     * [url] as the browser would follow it from the doc's page, [page]: an
     * absolute link as written, a relative one resolved against the page.
     * Null when the link goes nowhere a reader should be sent.
     */
    fun resolve(url: String, page: String): String? {
        val trimmed = url.trim()
        if (trimmed.isEmpty()) return null
        val colon = trimmed.indexOf(':')
        val hasScheme = colon > 0 && trimmed.indexOfAny(charArrayOf('/', '?', '#')).let { it == -1 || colon < it }
        if (hasScheme) {
            return trimmed.takeIf { trimmed.substring(0, colon).lowercase() in SAFE_SCHEMES }
        }
        return try {
            URI(page).resolve(URI(trimmed.replace(" ", "%20"))).toString()
        } catch (_: URISyntaxException) {
            null
        } catch (_: IllegalArgumentException) {
            null
        }
    }
}
