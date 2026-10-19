package cylist.app.docs.markdown

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class MarkdownLinksTest {
    private val page = "https://cylist.in/p/ATL/docs?doc=0190"

    @Test
    fun absoluteWebAndMailLinksAreFollowedAsWritten() {
        assertEquals("https://example.com/a?b=c#d", MarkdownLinks.resolve("https://example.com/a?b=c#d", page))
        assertEquals("http://example.com", MarkdownLinks.resolve("HTTP://example.com".lowercase(), page))
        assertEquals("mailto:someone@example.com", MarkdownLinks.resolve("mailto:someone@example.com", page))
    }

    @Test
    fun schemesTheWebDropsGoNowhere() {
        assertNull(MarkdownLinks.resolve("javascript:alert(1)", page))
        assertNull(MarkdownLinks.resolve("JavaScript:alert(1)", page))
        assertNull(MarkdownLinks.resolve("intent://scan#Intent;end", page))
        assertNull(MarkdownLinks.resolve("file:///etc/hosts", page))
        assertNull(MarkdownLinks.resolve("  ", page))
    }

    @Test
    fun relativeLinksResolveAgainstTheDocsPageAsTheBrowserWould() {
        assertEquals("https://cylist.in/p/ATL/engineering/migrations.md", MarkdownLinks.resolve("engineering/migrations.md", page))
        assertEquals("https://cylist.in/p/ATL/board", MarkdownLinks.resolve("/p/ATL/board", page))
        assertEquals("https://cylist.in/p/ATL/docs?doc=0190#ties", MarkdownLinks.resolve("#ties", page))
        assertEquals("https://cylist.in/p/ATL/a%20b", MarkdownLinks.resolve("a b", page))
    }
}
