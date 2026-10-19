package cylist.app.push

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** The shape the server sends: backend/app/services/push/messages.py, `data_for`. */
val questionData = mapOf(
    "kind" to "agent_session",
    "session_id" to "0199f1a2-0000-7000-8000-000000000001",
    "task" to "ATL-12",
    "task_title" to "Import the March schedule",
    "project" to "ATL",
    "state" to "waiting",
    "reason" to "question",
    "note" to "Which sheet is the March schedule, Sheet 1 or Mar-2026?",
    "client_name" to "ATL-12",
    "started_at" to "2026-10-19T09:00:00.123456+00:00",
    "state_changed_at" to "2026-10-19T09:06:00+00:00",
)

class AgentUpdateTest {
    @Test
    fun readsWhatTheServerSends() {
        val update = AgentUpdate.parse(questionData)

        assertEquals(
            AgentUpdate(
                sessionId = "0199f1a2-0000-7000-8000-000000000001",
                task = "ATL-12",
                taskTitle = "Import the March schedule",
                project = "ATL",
                state = AgentState.Waiting,
                reason = "question",
                note = "Which sheet is the March schedule, Sheet 1 or Mar-2026?",
                clientName = "ATL-12",
                startedAtMillis = 1_792_400_400_123L,
                changedAtMillis = 1_792_400_760_000L,
            ),
            update,
        )
    }

    @Test
    fun emptyValuesAreAbsent() {
        val update = AgentUpdate.parse(questionData + mapOf("state" to "working", "reason" to "", "note" to "", "client_name" to ""))

        assertEquals(AgentState.Working, update?.state)
        assertNull(update?.reason)
        assertNull(update?.note)
        assertNull(update?.clientName)
    }

    @Test
    fun anotherKindOfPushIsNotOurs() {
        assertNull(AgentUpdate.parse(questionData + ("kind" to "something_else")))
        assertNull(AgentUpdate.parse(questionData - "kind"))
    }

    @Test
    fun anUnknownStateOrMissingFieldIsRefusedRatherThanHalfDrawn() {
        assertNull(AgentUpdate.parse(questionData + ("state" to "paused")))
        assertNull(AgentUpdate.parse(questionData - "session_id"))
        assertNull(AgentUpdate.parse(questionData + ("task" to "")))
        assertNull(AgentUpdate.parse(questionData + ("started_at" to "yesterday")))
    }
}
