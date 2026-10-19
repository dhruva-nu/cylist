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

    @Test
    fun aSwipeCommitsPast72dpOrOnAFlickAndOtherwiseSpringsBack() {
        // Far enough, at any speed; a drag to the left goes to the next column.
        assertEquals(1, swipeStep(dx = -72f, velocity = 0f, hasPrevious = true, hasNext = true))
        assertEquals(-1, swipeStep(dx = 90f, velocity = 0f, hasPrevious = true, hasNext = true))
        // Short and slow springs back.
        assertEquals(0, swipeStep(dx = -71f, velocity = -0.4f, hasPrevious = true, hasNext = true))
        // A flick: past 28dp and faster than 0.5 dp/ms, in the same direction.
        assertEquals(1, swipeStep(dx = -30f, velocity = -0.6f, hasPrevious = true, hasNext = true))
        assertEquals(0, swipeStep(dx = -27f, velocity = -2f, hasPrevious = true, hasNext = true))
        assertEquals(0, swipeStep(dx = -30f, velocity = 0.6f, hasPrevious = true, hasNext = true))
        // Nothing past the first or last column, however far.
        assertEquals(0, swipeStep(dx = 200f, velocity = 3f, hasPrevious = false, hasNext = true))
        assertEquals(0, swipeStep(dx = -200f, velocity = -3f, hasPrevious = true, hasNext = false))
    }

    @Test
    fun theEndsRubberBandAQuarterOfTheDrag() {
        assertEquals(40f, swipeFollow(40f, hasPrevious = true, hasNext = false), 0f)
        assertEquals(10f, swipeFollow(40f, hasPrevious = false, hasNext = true), 0f)
        assertEquals(-10f, swipeFollow(-40f, hasPrevious = true, hasNext = false), 0f)
    }

    @Test
    fun aGestureIsSidewaysOnlyWhenClearlySo() {
        assertNull(swipeAxisOf(5f, 5f))
        assertEquals(SwipeAxis.Sideways, swipeAxisOf(8f, 0f))
        assertEquals(SwipeAxis.Sideways, swipeAxisOf(-9f, 7f))
        // Not more than 1.2 times as sideways: the list scrolls.
        assertEquals(SwipeAxis.UpDown, swipeAxisOf(7f, 6f))
        assertEquals(SwipeAxis.UpDown, swipeAxisOf(0f, -8f))
    }

    @Test
    fun theAssigneeIsYouOrTheirFirstName() {
        assertEquals("You", assigneeLabel(me.id, me.name, me.id))
        assertEquals("Aditi", assigneeLabel(aditi.id, aditi.name, me.id))
        assertEquals("Aditi", assigneeLabel(aditi.id, aditi.name, null))
        assertEquals("Agent", assigneeLabel(aditi.id, "Agent", me.id))
    }

    @Test
    fun theSnackbarSaysWhenTheFilterHidesTheCard() {
        assertEquals("HWL-7 is now Aditi K's.", reassignedText("HWL-7", "Aditi K", toViewer = false, hidden = false))
        assertEquals("HWL-7 is now Aditi K's. Your filter hides it.", reassignedText("HWL-7", "Aditi K", toViewer = false, hidden = true))
        assertEquals("HWL-7 is now yours.", reassignedText("HWL-7", "Dhruva N", toViewer = true, hidden = false))
    }

    @Test
    fun openCardsCountOnlyUnfinishedCardsOnTheBoard() {
        val done = UUID.randomUUID()
        val open = openCardsOf(
            listOf(
                task(assignee = aditi),
                task(assignee = aditi, subtaskAssignees = listOf(me)),
                task(assignee = aditi, finishedAt = "2026-10-02T10:00:00Z"),
                task(assignee = aditi, columnId = done),
                task(assignee = me, status = TaskStatus.cancelled),
            ),
            lastColumnId = done,
        )
        assertEquals(2, open[aditi.id])
        assertNull(open[me.id])
        assertEquals("1 open card", openCardsText(1))
        assertEquals("0 open cards", openCardsText(0))
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
        columnId: UUID = UUID.randomUUID(),
    ) = TaskRead(
        id = UUID.randomUUID(), projectId = UUID.randomUUID(), reference = "ATL-1", number = 1,
        parentId = parentId, parentReference = null, subNumber = null, columnId = columnId, position = 0,
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
