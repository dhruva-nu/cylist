package cylist.api

import java.util.UUID

/**
 * Every row of a paged list, read by following each page's `next` cursor.
 *
 * The server hands out every list a page at a time (CYLIST-130). Most screens
 * here want one page; the few that need the whole of a short list — the
 * project picker, the people a plan can name — read it with this.
 *
 * [read] fetches the page after the given cursor (null for the first) and
 * returns its rows and its `next`. [maxPages] is a backstop rather than a
 * limit anyone should meet: a server that kept answering with a cursor would
 * otherwise keep the phone reading forever.
 */
fun <T> everyPage(maxPages: Int = 100, read: (after: UUID?) -> Pair<List<T>, UUID?>): List<T> {
    val rows = mutableListOf<T>()
    var after: UUID? = null
    repeat(maxPages) {
        val (page, next) = read(after)
        rows += page
        after = next ?: return rows
    }
    return rows
}
