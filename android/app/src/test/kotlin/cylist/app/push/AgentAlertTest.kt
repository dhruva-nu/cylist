package cylist.app.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AgentAlertTest {
    private val question = checkNotNull(AgentUpdate.parse(questionData))

    @Test
    fun workingIsASilentLiveUpdateCountingUpFromTheStart() {
        val alert = AgentAlert.of(question.copy(state = AgentState.Working, reason = null, note = null))

        assertEquals(AlertChannel.Running, alert.channel)
        assertEquals("Working on ATL-12", alert.title)
        assertEquals("Import the March schedule", alert.text)
        assertTrue(alert.ongoing && alert.promoted)
        assertEquals(question.startedAtMillis, alert.chronometerSinceMillis)
        assertNull(alert.chip)
    }

    @Test
    fun aQuestionRingsAndShowsTheQuestion() {
        val alert = AgentAlert.of(question)

        assertEquals(AlertChannel.NeedsYou, alert.channel)
        assertEquals("Agent needs you on ATL-12", alert.title)
        assertEquals("Which sheet is the March schedule, Sheet 1 or Mar-2026?", alert.text)
        assertEquals("Needs you", alert.chip)
        assertTrue(alert.ongoing && alert.promoted)
    }

    @Test
    fun aPermissionRingsToo() {
        val alert = AgentAlert.of(question.copy(reason = "permission", note = "Run alembic upgrade head"))

        assertEquals(AlertChannel.NeedsYou, alert.channel)
        assertEquals("Agent wants permission on ATL-12", alert.title)
        assertEquals("Run alembic upgrade head", alert.text)
    }

    @Test
    fun aTurnThatEndedIsWorthSeeingButNotRinging() {
        val alert = AgentAlert.of(question.copy(reason = "turn_ended", note = null))

        assertEquals(AlertChannel.Running, alert.channel)
        assertEquals("Agent is waiting on ATL-12", alert.title)
        assertEquals("Your turn", alert.chip)
        assertTrue(alert.promoted)
    }

    @Test
    fun finishedEndsTheLiveUpdate() {
        val alert = AgentAlert.of(question.copy(state = AgentState.Done, reason = "session_ended", note = null))

        assertEquals(AlertChannel.Finished, alert.channel)
        assertEquals("Agent finished ATL-12", alert.title)
        assertFalse(alert.ongoing)
        assertFalse(alert.promoted)
    }

    @Test
    fun aLostConnectionSaysSo() {
        val alert = AgentAlert.of(question.copy(state = AgentState.Done, reason = "connection_lost"))

        assertEquals("Agent lost touch on ATL-12", alert.title)
    }
}
