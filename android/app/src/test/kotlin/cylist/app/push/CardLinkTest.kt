package cylist.app.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class CardLinkTest {
    private val session = "0199f1a2-0000-7000-8000-000000000001"

    @Test
    fun roundTripsThroughItsUri() {
        val link = CardLink("ATL-12", session)

        assertEquals("cylist://card/ATL-12?session=$session", link.toUri())
        assertEquals(link, CardLink.parse(link.toUri()))
    }

    @Test
    fun aSubTaskAndNoSessionAreFine() {
        assertEquals(CardLink("ATL-12-2", null), CardLink.parse("cylist://card/ATL-12-2"))
    }

    @Test
    fun anythingElseIsNotALink() {
        assertNull(CardLink.parse(null))
        assertNull(CardLink.parse("https://cylist.in/ATL-12"))
        assertNull(CardLink.parse("cylist://card/../../etc"))
        assertNull(CardLink.parse("cylist://card/"))
    }

    @Test
    fun aMalformedSessionIsDroppedButTheCardKept() {
        assertEquals(CardLink("ATL-12", null), CardLink.parse("cylist://card/ATL-12?session=nope"))
    }
}
