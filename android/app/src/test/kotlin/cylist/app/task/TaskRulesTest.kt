package cylist.app.task

import cylist.api.generated.models.AgentFeedKind
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.AgentSessionState
import cylist.api.generated.models.Channel
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.ColumnDueDateRead
import cylist.api.generated.models.HistoryEntry
import cylist.api.generated.models.TaskStatus
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneOffset
import java.util.UUID

class TaskRulesTest {
    private val now: Instant = Instant.parse("2026-10-19T09:05:00Z")

    // --- TaskRef.tsx ---

    @Test
    fun aPastedUrlReadsAsTheIdentifierInsideIt() {
        assertEquals(RefDisplay("ATL-41", "https://acme.atlassian.net/browse/ATL-41"), jiraRefDisplay("https://acme.atlassian.net/browse/ATL-41"))
        assertEquals("GLSSW-7", jiraRefDisplay("https://acme.atlassian.net/jira/board?selectedIssue=glssw-7").label)
        assertEquals(RefDisplay("#212", "https://github.com/dhruva-nu/cylist/pull/212"), prRefDisplay("https://github.com/dhruva-nu/cylist/pull/212"))
        assertEquals("#9", prRefDisplay("https://gitlab.com/g/p/-/merge_requests/9").label)
        assertEquals("cylist", prRepoOf("https://github.com/dhruva-nu/cylist/pull/212"))
        assertEquals("p", prRepoOf("https://gitlab.com/g/p/-/merge_requests/9"))
    }

    @Test
    fun aTypedReferenceIsShownAsTypedWithNothingToFollow() {
        assertEquals(RefDisplay("ATL-41", null), jiraRefDisplay("ATL-41"))
        assertEquals(RefDisplay("#212", null), prRefDisplay("#212"))
        assertNull(prRepoOf("#212"))
        // Not a link however it is spelled: nothing but http(s) is ever followed.
        assertNull(jiraRefDisplay("javascript:alert(1)").href)
    }

    // --- agentFeed.ts ---

    @Test
    fun feedLinesAreLabelledAndCodeIsSetApart() {
        assertEquals(
            listOf("Said", "Read", "Edit", "Ran", "Got", "Failed", "Tool"),
            listOf(AgentFeedKind.said, AgentFeedKind.read, AgentFeedKind.edit, AgentFeedKind.run, AgentFeedKind.output, AgentFeedKind.error, AgentFeedKind.tool).map(::feedLabel),
        )
        assertFalse(isCodeLike(AgentFeedKind.said))
        assertTrue(isCodeLike(AgentFeedKind.run))
        assertEquals("14:31", feedClock("2026-10-19T09:01:02Z", ZoneOffset.ofHoursMinutes(5, 30)))
        assertEquals("", feedClock("not a time", ZoneOffset.UTC))
    }

    // --- agentState.ts ---

    @Test
    fun silenceIsSaidTheWayAPersonWould() {
        assertEquals("0m", silentFor("2026-10-19T09:04:30Z", now))
        assertEquals("7m", silentFor("2026-10-19T08:58:00Z", now))
        assertEquals("3h", silentFor("2026-10-19T06:00:00Z", now))
        assertEquals("2d", silentFor("2026-10-17T09:00:00Z", now))
        assertEquals("0m", silentFor("2026-10-19T10:00:00Z", now))
    }

    @Test
    fun theNowLineIsTheNoteOrThePlainestTrueThing() {
        assertEquals("Running pytest", nowLine(session(note = "Running pytest")))
        assertEquals("Working", nowLine(session()))
        assertEquals("Waiting for permission", nowLine(session(AgentSessionState.waiting, AgentSessionReason.permission)))
        assertEquals("Waiting for an answer", nowLine(session(AgentSessionState.waiting, AgentSessionReason.question)))
        assertEquals("Waiting for your reply", nowLine(session(AgentSessionState.waiting, AgentSessionReason.turn_ended)))
        assertNull(nowLine(session(AgentSessionState.done, note = "left over")))
    }

    @Test
    fun thePillSaysTheStateAndSinceWhen() {
        assertEquals(SessionPill(SessionTone.Working, "Working", "working since", "2026-10-19T08:30:00Z"), sessionPill(session()))
        val asking = prompt()
        assertEquals(
            SessionPill(SessionTone.Waiting, "Waiting: a question for you", "waiting since", asking.askedAt),
            sessionPill(session(AgentSessionState.waiting, AgentSessionReason.question, prompt = asking)),
        )
        assertEquals("Waiting: permission", sessionPill(session(AgentSessionState.waiting, prompt = prompt(AgentPromptKind.permission))).label)
        assertEquals("Waiting: idle", sessionPill(session(AgentSessionState.waiting, AgentSessionReason.idle)).label)
        assertEquals(SessionTone.Stale, sessionPill(session(reason = AgentSessionReason.connection_lost, endedAt = "2026-10-19T09:00:00Z")).tone)
        assertEquals("Moved to another card", sessionPill(session(reason = AgentSessionReason.moved, endedAt = "2026-10-19T09:00:00Z")).label)
        assertEquals("Finished", sessionPill(session(AgentSessionState.done, endedAt = "2026-10-19T09:00:00Z")).label)
    }

    @Test
    fun onlyAnOpenUnexpiredPromptCanBeAnswered() {
        assertTrue(isAnswerable(prompt(), now))
        assertFalse(isAnswerable(prompt(expiresAt = "2026-10-19T09:04:59Z"), now))
        assertTrue(isAnswerable(prompt(expiresAt = "2026-10-19T09:04:59Z"), null))
        assertFalse(isAnswerable(prompt(status = AgentPromptStatus.answered), now))
        assertFalse(isAnswerable(null, now))
    }

    @Test
    fun whoAnsweredIsYouToThemAndTheirNameToEveryoneElse() {
        val aditi = UUID.randomUUID()
        val answered = prompt(status = AgentPromptStatus.answered, answeredBy = "Aditi K", answeredById = aditi, answer = "40")
        assertEquals("You", answeredBy(answered, aditi))
        assertEquals("Aditi K", answeredBy(answered, UUID.randomUUID()))
        assertEquals("Answered", answeredBy(prompt(status = AgentPromptStatus.answered), null))
    }

    // --- promptAnswer.ts ---

    @Test
    fun theHeadingSaysWhatIsAskedAndHowLongAgo() {
        assertEquals("Question from the agent · 5m ago", promptHeading(prompt(), now))
        assertEquals("Permission asked · just now", promptHeading(prompt(AgentPromptKind.permission, askedAt = "2026-10-19T09:04:40Z"), now))
    }

    @Test
    fun aReplyIsSentOnlyWhereOneIsTakenAndSomethingWasTyped() {
        assertEquals(AgentPromptAnswer(text = "40, please"), replyAnswer(prompt(), "  40, please "))
        assertNull(replyAnswer(prompt(), "   "))
        assertNull(replyAnswer(prompt(AgentPromptKind.permission), "yes"))
    }

    @Test
    fun aPermissionsYesIsGreenAndItsNoRed() {
        val permission = prompt(AgentPromptKind.permission)
        assertEquals(ChoiceTone.Go, choiceTone(permission, "allow_once"))
        assertEquals(ChoiceTone.Plain, choiceTone(permission, "allow_session"))
        assertEquals(ChoiceTone.Danger, choiceTone(permission, "deny"))
        assertEquals(ChoiceTone.Plain, choiceTone(prompt(), "deny"))
    }

    // --- moves.py, useBoardDrag.ts, narrowBoard.ts ---

    @Test
    fun aCardWithOpenWorkCannotGoToTheLastColumn() {
        val card = detail(
            subtasks = listOf(subtask("ATL-1-1", finishedAt = "2026-10-18T10:00:00Z"), subtask("ATL-1-2"), subtask("ATL-1-3", status = TaskStatus.cancelled)),
            checklist = listOf(item("Write tests"), item("Tidy", ChecklistState.done), item("Dropped", ChecklistState.cancelled)),
        )
        assertEquals(listOf("ATL-1-2", "Write tests"), outstandingWork(card))
        assertEquals(
            MovePlan.Refused("ATL-1 still has 2 unfinished sub-tasks: ATL-1-2, Write tests. Finish or cancel each of them before moving this card to Done."),
            planMove(card, board, emptyList(), board[3].id, "Done"),
        )
        // Anywhere else is fine.
        assertEquals(MovePlan.Go, planMove(card, board, emptyList(), board[2].id, null))
    }

    @Test
    fun oneOpenThingIsSaidInTheSingular() {
        val card = detail(checklist = listOf(item("Write tests")))
        assertEquals(
            MovePlan.Refused("ATL-1 still has 1 unfinished sub-task: Write tests. Finish or cancel each of them before moving this card to Done."),
            planMove(card, board, emptyList(), board[3].id, null),
        )
    }

    @Test
    fun aSettledCardGoesToTheLastColumnOnTheOutcomeNamed() {
        val card = detail(subtasks = listOf(subtask("ATL-1-1", finishedAt = "2026-10-18T10:00:00Z")))
        assertEquals(MovePlan.Go, planMove(card, board, emptyList(), board[3].id, "In prod"))
    }

    @Test
    fun outcomesBelongToTheLastColumnAndTheTemplateMayNarrowThem() {
        val card = detail()
        assertEquals(listOf("Done", "Cancelled", "In prod"), outcomesFor(card, board[3], board, emptyList()))
        assertEquals(emptyList<String>(), outcomesFor(card, board[2], board, emptyList()))
        val bugs = template("Bug", board, mapOf(board[3] to listOf("in prod", "Done")))
        val templated = detail(templateId = bugs.id)
        assertEquals(listOf("Done", "In prod"), outcomesFor(templated, board[3], board, listOf(bugs)))
        // A template naming nothing the column still has means all of them.
        val stale = template("Old", board, mapOf(board[3] to listOf("Shipped")))
        assertEquals(board[3].outcomes, outcomesFor(detail(templateId = stale.id), board[3], board, listOf(stale)))
    }

    @Test
    fun aDifferentOutcomeInTheLastColumnIsAMoveAndTheSameOneIsNot() {
        val done = detail(columnId = board[3].id, outcome = "Done", finishedAt = "2026-10-18T10:00:00Z")
        assertEquals(MovePlan.Stay, planMove(done, board, emptyList(), board[3].id, "done"))
        assertEquals(MovePlan.Go, planMove(done, board, emptyList(), board[3].id, "In prod"))
        assertEquals(MovePlan.Stay, planMove(detail(), board, emptyList(), board[1].id, null))
    }

    @Test
    fun takingAFinishedCardBackOutIsAskedFirst() {
        val done = detail(columnId = board[3].id, outcome = "Done", finishedAt = "2026-10-18T10:00:00Z")
        assertEquals(MovePlan.Reopen(from = "Done", to = "Review"), planMove(done, board, emptyList(), board[2].id, null))
    }

    @Test
    fun aTemplateKeepsItsCardsToItsOwnColumns() {
        val spikes = template("Spike", listOf(board[0], board[1]))
        val card = detail(templateId = spikes.id)
        assertEquals(MovePlan.Refused("Spike cards do not go to Review."), planMove(card, board, listOf(spikes), board[2].id, null))
        assertEquals(MovePlan.Go, planMove(card, board, listOf(spikes), board[0].id, null))
    }

    @Test
    fun subTasksAndStoredCardsAreNotOnTheBoardToMove() {
        assertTrue(planMove(detail(columnId = null, parentId = UUID.randomUUID()), board, emptyList(), board[2].id, null) is MovePlan.Refused)
        assertTrue(planMove(detail(archivedAt = "2026-10-18T10:00:00Z"), board, emptyList(), board[2].id, null) is MovePlan.Refused)
    }

    // --- useSubtaskTicking.ts ---

    @Test
    fun aTapTicksOrUnticksButLeavesACancelledItemAlone() {
        assertEquals(ChecklistState.done, nextChecklistState(ChecklistState.`open`))
        assertEquals(ChecklistState.`open`, nextChecklistState(ChecklistState.done))
        assertNull(nextChecklistState(ChecklistState.cancelled))
    }

    // --- The rest ---

    @Test
    fun theReporterIsWhoeverCreatedTheCard() {
        assertEquals("Aditi K", reporterOf(history("task.created", "Aditi K")))
        // The oldest entry is not a creation when the history was trimmed: say nothing rather than guess.
        assertNull(reporterOf(history("task.moved", "Rohan S")))
        assertNull(reporterOf(null))
    }

    @Test
    fun theDueDateNamesTheColumnItBelongsTo() {
        val review = board[2]
        val card = detail().copy(
            dueDate = "2026-10-30",
            nextDueDate = "2026-10-20",
            columnDueDates = listOf(
                ColumnDueDateRead(board[1].id, "2026-10-10", "In progress", met = true),
                ColumnDueDateRead(review.id, "2026-10-20", " Review ", met = false),
            ),
        )
        assertEquals("Review", dueColumnOf(card))
        assertNull(dueColumnOf(card.copy(nextDueDate = "2026-10-30")))
        assertNull(dueColumnOf(card.copy(nextDueDate = null)))
    }

    @Test
    fun commentsSayHowLongAgo() {
        assertEquals("just now", ago("2026-10-19T09:04:40Z", now, ZoneOffset.UTC))
        assertEquals("1 minute ago", ago("2026-10-19T09:04:00Z", now, ZoneOffset.UTC))
        assertEquals("5 minutes ago", ago("2026-10-19T09:00:00Z", now, ZoneOffset.UTC))
        assertEquals("2 hours ago", ago("2026-10-19T07:00:00Z", now, ZoneOffset.UTC))
        assertEquals("2 days ago", ago("2026-10-17T09:00:00Z", now, ZoneOffset.UTC))
        assertEquals("1 Aug 2026", ago("2026-08-01T09:00:00Z", now, ZoneOffset.UTC))
    }

    @Test
    fun aDayIsSaidInFull() {
        assertEquals("29 Aug 2026", dayOf("2026-08-29"))
        assertEquals("soon", dayOf("soon"))
    }

    @Test
    fun theStageNoteSaysWhereInTheColumnTheCardIs() {
        assertEquals("Review, 2 of 3", stageNote(detail(subStatuses = listOf("Build", "Review", "QA"), subStatusIndex = 1)))
        assertNull(stageNote(detail()))
    }

    @Test
    fun aMentionIsAMembersNameAfterAnAt() {
        val names = listOf("Aditi", "Aditi K", "Rohan S")
        val text = "@Aditi K can you check? cc @rohan s, not deploy@Aditi K nor @Aditya"
        assertEquals(listOf(0..7, 27..34), mentionRanges(text, names))
        assertEquals(emptyList<IntRange>(), mentionRanges("nobody here", names))
    }

    private fun history(verb: String, actor: String) = HistoryEntry(
        id = UUID.randomUUID(), occurredAt = "2026-10-01T00:00:00Z", actorLabel = actor, channel = Channel.web,
        verb = verb, summary = "", payload = emptyMap(),
    )
}
