package cylist.app.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class AgentPillTest {
    private val asking = checkNotNull(AgentUpdate.parse(questionData))

    private fun working(session: String, changedAt: Long) = asking.copy(
        sessionId = session,
        task = "ATL-$session",
        state = AgentState.Working,
        reason = null,
        changedAtMillis = changedAt,
    )

    @Test
    fun noAgentsNoPill() {
        assertNull(AgentPill.of(emptyList()).leader)
    }

    @Test
    fun oneAgentIsTheRobot() {
        val pill = AgentPill.of(listOf(working("1", 10)))

        assertEquals(AgentPill("1", PillMark.Robot), pill)
        assertEquals("robot", pill.roleOf("1"))
    }

    @Test
    fun severalPutTheNewestInThePillWithTheCountInItsIcon() {
        val pill = AgentPill.of(listOf(working("1", 10), working("2", 30), working("3", 20)))

        assertEquals(AgentPill("2", PillMark.Count("3")), pill)
        assertEquals("count:3", pill.roleOf("2"))
        assertEquals("shade", pill.roleOf("1"))
    }

    @Test
    fun aQuestionTakesThePillFromNewerWork() {
        val question = asking.copy(sessionId = "q", changedAtMillis = 5)
        val pill = AgentPill.of(listOf(working("1", 10), question, working("2", 30)))

        assertEquals(AgentPill("q", PillMark.Asks), pill)
    }

    @Test
    fun aTurnThatEndedDoesNotTakeThePill() {
        val idle = asking.copy(sessionId = "idle", reason = "turn_ended", changedAtMillis = 5)

        assertEquals("1", AgentPill.of(listOf(working("1", 10), idle)).leader)
    }

    @Test
    fun pastNineItSaysNinePlus() {
        val pill = AgentPill.of((1..12).map { working("$it", it.toLong()) })

        assertEquals(PillMark.Count("9+"), pill.mark)
    }
}
