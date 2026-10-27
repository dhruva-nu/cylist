package cylist.app.board

import org.junit.Assert.assertEquals
import org.junit.Test
import java.util.UUID

class RememberedPeopleTest {
    private val me = UUID.randomUUID()
    private val aditi = UUID.randomUUID()
    private val gone = UUID.randomUUID()
    private val members = setOf(me, aditi)

    @Test
    fun nothingRememberedOpensOnYou() = assertEquals(setOf(me), openingPick(null, me, members))

    @Test
    fun nothingRememberedAndNotOnTheProjectOpensOnEveryone() = assertEquals(emptySet<UUID>(), openingPick(null, me, setOf(aditi)))

    @Test
    fun everyoneIsRemembered() = assertEquals(emptySet<UUID>(), openingPick("everyone", me, members))

    @Test
    fun aPickIsRemembered() = assertEquals(setOf(aditi), openingPick(pickText(setOf(aditi)), me, members))

    @Test
    fun peopleWhoLeftAreDropped() = assertEquals(setOf(aditi), openingPick("$gone,$aditi", me, members))

    @Test
    fun aPickOfOnlyLeaversOpensOnYou() = assertEquals(setOf(me), openingPick("$gone", me, members))

    @Test
    fun garbageIsIgnored() = assertEquals(setOf(me), openingPick("not-an-id", me, members))

    @Test
    fun pickTextRoundTrips() {
        assertEquals("everyone", pickText(emptySet()))
        assertEquals(setOf(me, aditi), openingPick(pickText(setOf(me, aditi)), null, members))
    }
}
