package cylist.api

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.UUID

class PagingTest {
    @Test
    fun `follows next until a page has none`() {
        val cursors = listOf(UUID.randomUUID(), UUID.randomUUID())
        val asked = mutableListOf<UUID?>()
        val rows = everyPage { after ->
            asked += after
            when (after) {
                null -> listOf(1, 2) to cursors[0]
                cursors[0] -> listOf(3) to cursors[1]
                else -> listOf(4) to null
            }
        }
        assertEquals(listOf(1, 2, 3, 4), rows)
        assertEquals(listOf(null, cursors[0], cursors[1]), asked)
    }

    @Test
    fun `stops at the backstop when the cursor never ends`() {
        var calls = 0
        val rows = everyPage(maxPages = 3) { calls++; listOf(calls) to UUID.randomUUID() }
        assertEquals(listOf(1, 2, 3), rows)
    }
}
