package cylist.app.board

import cylist.api.generated.models.AgentPresence
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.PersonKind
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.PrRef
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate
import java.util.UUID

class BoardRulesTest {
    private val today = LocalDate.of(2026, 10, 19)
    private val me = person("Dhruva N")
    private val aditi = person("Aditi K")

    @Test
    fun dueSaysHowNearInTheCardsWords() {
        assertEquals(Due(DueKind.Late, "3d late", "3 days late"), dueOf(task(due = "2026-10-16"), today))
        assertEquals(DueKind.Today, dueOf(task(due = "2026-10-19"), today)?.kind)
        assertEquals("Tomorrow", dueOf(task(due = "2026-10-20"), today)?.text)
        assertEquals(Due(DueKind.Soon, "23 Oct", "Due 23 Oct"), dueOf(task(due = "2026-10-23"), today))
        assertEquals(DueKind.Later, dueOf(task(due = "2026-11-04"), today)?.kind)
        assertNull(dueOf(task(due = null), today))
    }

    @Test
    fun aFinishedOrCancelledCardIsNeverLate() {
        assertNull(dueOf(task(due = "2026-10-01", finishedAt = "2026-10-02T10:00:00Z"), today))
        assertNull(dueOf(task(due = "2026-10-01", status = TaskStatus.cancelled), today))
    }

    @Test
    fun theAgentLineIsThereOnlyWhileAnAgentIs() {
        assertNull(agentLineOf(null, null))
        assertNull(agentLineOf(presence(AgentPresence.State.done), null))
        assertEquals(AgentLine(false, "Agent working", null), agentLineOf(presence(AgentPresence.State.working), null))
        assertEquals(AgentLine(false, "writing the test", null), agentLineOf(presence(AgentPresence.State.working, note = "writing the test"), null))
        assertEquals(
            AgentLine(true, "Agent needs you", "a question"),
            agentLineOf(presence(AgentPresence.State.waiting, reason = AgentSessionReason.question), null),
        )
        assertEquals(
            AgentLine(true, "2 agents · one needs you", "waiting for your reply"),
            agentLineOf(presence(AgentPresence.State.waiting, count = 2, reason = AgentSessionReason.turn_ended), null),
        )
    }

    @Test
    fun thePeopleFilterCountsSubtaskOwnersAndOpensOnYou() {
        val shared = task(assignee = aditi, subtaskAssignees = listOf(me))
        assertTrue(ownedByAny(shared, setOf(me.id)))
        assertTrue(ownedByAny(shared, emptySet()))
        assertFalse(ownedByAny(task(assignee = aditi), setOf(me.id)))

        val owned = cardsOwned(listOf(shared, task(assignee = aditi), task(assignee = aditi, subtaskAssignees = listOf(aditi))))
        assertEquals(3, owned[aditi.id])
        assertEquals(1, owned[me.id])
        assertEquals(listOf(me, aditi), facesInOrder(listOf(aditi, me), { it.id }, { it.name }, owned, me.id))
    }

    @Test
    fun theBoardHidesSubtasksCancelledAndArchivedCards() {
        assertTrue(onBoard(task()))
        assertFalse(onBoard(task(status = TaskStatus.cancelled)))
        assertFalse(onBoard(task(parentId = UUID.randomUUID())))
        assertFalse(onBoard(task(archivedAt = "2026-10-01T00:00:00Z")))
        assertTrue(isHeld(task(status = TaskStatus.hold)))
    }

    @Test
    fun aCardShowsItsFurthestBehindPullRequest() {
        assertNull(prStateOf(task()))
        assertEquals(PrState.raised, prStateOf(task(prs = listOf(PrState.merged, null))))
        assertEquals(PrState.approved, prStateOf(task(prs = listOf(PrState.merged, PrState.approved))))
    }

    @Test
    fun initialsAreTheFirstTwoWords() {
        assertEquals("DN", initialsOf("Dhruva N"))
        assertEquals("A", initialsOf("Agent"))
        assertEquals("?", initialsOf("  "))
    }

    private fun person(name: String) = PersonRead(
        id = UUID.randomUUID(), name = name, kind = PersonKind.team, title = "", responsibilities = "",
        email = null, colour = "#4A7B8C", archivedAt = null, createdAt = "2026-10-01T00:00:00Z",
        isAgent = false, hasAccount = true, inviteIsPending = false,
    )

    private fun presence(
        state: AgentPresence.State,
        count: Int = 1,
        reason: AgentSessionReason? = null,
        note: String? = null,
    ) = AgentPresence(
        state = state, count = count, reason = reason, clientName = null, note = note,
        since = "2026-10-19T09:00:00Z", lastSeenAt = "2026-10-19T09:05:00Z",
    )

    private fun task(
        due: String? = null,
        status: TaskStatus = TaskStatus.active,
        finishedAt: String? = null,
        assignee: PersonRead = me,
        subtaskAssignees: List<PersonRead> = emptyList(),
        parentId: UUID? = null,
        archivedAt: String? = null,
        prs: List<PrState?> = emptyList(),
    ) = TaskRead(
        id = UUID.randomUUID(), projectId = UUID.randomUUID(), reference = "ATL-1", number = 1,
        parentId = parentId, parentReference = null, subNumber = null, columnId = UUID.randomUUID(), position = 0,
        title = "A card", description = "", type = TaskType.feature, priority = TaskPriority.p3,
        subStatuses = emptyList(), subStatusIndex = null, dueDate = due, columnDueDates = emptyList(), nextDueDate = due,
        assignee = assignee, status = status, templateId = null, templateName = null,
        goalId = null, goalReference = null, goalName = null, goalColour = null, jiraRef = null,
        prRefs = prs.map { PrRef(url = "https://github.com/o/r/pull/1", state = it) },
        waitingOn = emptyList(), commentCount = 0, checklist = emptyList(), outcome = null, outcomeIndex = null,
        finishedAt = finishedAt, openSubtaskCount = 0, subtaskCount = 0, subtaskAssignees = subtaskAssignees,
        createdAt = "2026-10-01T00:00:00Z", archivedAt = archivedAt,
    )
}
