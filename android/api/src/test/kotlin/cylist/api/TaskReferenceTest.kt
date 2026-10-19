package cylist.api

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class TaskReferenceTest {
    @Test
    fun parsesATask() {
        assertEquals(TaskReference("CYLIST", 100), TaskReference.parse("CYLIST-100"))
    }

    @Test
    fun parsesASubTask() {
        assertEquals(TaskReference("ATL", 41, 2), TaskReference.parse("ATL-41-2"))
    }

    @Test
    fun keysAreHeldUpperCase() {
        assertEquals(TaskReference("ATL", 41), TaskReference.parse(" atl-41 "))
    }

    @Test
    fun printsBackTheSameReference() {
        assertEquals("ATL-41-2", TaskReference("ATL", 41, 2).toString())
        assertEquals("CYLIST-100", TaskReference("CYLIST", 100).toString())
    }

    @Test
    fun rejectsWhatIsNotAReference() {
        listOf("", "ATL", "ATL-", "A-1", "TOOLONGKEY-1", "1TL-4", "ATL-0", "ATL-41-0", "ATL-41-2-3", "ATL 41")
            .forEach { assertNull(it, TaskReference.parse(it)) }
    }

    @Test
    fun rejectsANumberTooLargeForAnInt() {
        assertNull(TaskReference.parse("ATL-99999999999"))
    }
}
