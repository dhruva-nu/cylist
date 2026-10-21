package cylist.app.push

import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test
import java.util.UUID

class AgentSessionPickTest {
    private fun row(state: AgentSessionState, changed: String) = AgentSessionRead(
        id = UUID.randomUUID(),
        taskId = UUID.randomUUID(),
        actorLabel = "claude",
        clientName = null,
        state = state,
        reason = null,
        note = null,
        startedAt = "2026-10-19T09:00:00Z",
        stateChangedAt = changed,
        lastSeenAt = changed,
        endedAt = null,
        dismissedAt = null,
    )

    private val done = row(AgentSessionState.done, "2026-10-19T09:03:00Z")
    private val working = row(AgentSessionState.working, "2026-10-19T09:01:00Z")
    private val waiting = row(AgentSessionState.waiting, "2026-10-19T09:02:00Z")

    @Test
    fun theNotificationSOwnSessionWins() {
        assertEquals(done, AgentSessionViewModel.pick(listOf(waiting, working, done), done.id.toString()))
    }

    @Test
    fun otherwiseTheOneThatNeedsYouThenTheOneWorking() {
        assertEquals(waiting, AgentSessionViewModel.pick(listOf(working, done, waiting), "gone"))
        assertEquals(working, AgentSessionViewModel.pick(listOf(done, working), null))
        assertEquals(done, AgentSessionViewModel.pick(listOf(done), null))
        assertNull(AgentSessionViewModel.pick(emptyList(), null))
    }
}
