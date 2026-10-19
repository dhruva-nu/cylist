package cylist.app.today

import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TodoRead
import cylist.api.generated.models.TodoState
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.LocalDate

/** `today.test.ts` ported, then the dates and the wording the phone shares with the web. */
class TodayRulesTest {
    private val today = LocalDate.of(2026, 9, 8)

    private fun refs(tasks: List<TaskRead>) = tasks.map { it.reference }

    @Test
    fun todaysWorkIsWhatIsDueTodayOrLateAndNothingLater() {
        val work = todaysWork(
            listOf(card("A-1"), card("A-2", due = "2026-09-07"), card("A-3", due = "2026-09-09"), card("A-4", due = "2026-10-01")),
            BOARD, ME.id, today,
        )
        assertEquals(listOf("A-2"), refs(work.overdue))
        assertEquals(listOf("A-1"), refs(work.due))
        assertEquals(TodayWork(emptyList(), emptyList()), todaysWork(listOf(card("A-1", due = null, priority = TaskPriority.p0)), BOARD, ME.id, today))
    }

    @Test
    fun todaysWorkLeavesOutFinishedCancelledAndHeldButKeepsBlocked() {
        assertEquals(listOf("A-2"), refs(todaysWork(listOf(card("A-1", column = DONE.id), card("A-2", column = DOING.id)), BOARD, ME.id, today).due))
        assertTrue(todaysWork(listOf(card("A-1", status = TaskStatus.cancelled)), BOARD, ME.id, today).due.isEmpty())
        val held = todaysWork(listOf(card("A-1", status = TaskStatus.hold), card("A-2", status = TaskStatus.hold, due = "2026-08-18")), BOARD, ME.id, today)
        assertTrue(held.due.isEmpty() && held.overdue.isEmpty())
        val blocked = todaysWork(listOf(card("A-1", status = TaskStatus.blocked, due = "2026-09-01"), card("A-2", status = TaskStatus.blocked)), BOARD, ME.id, today)
        assertEquals(listOf("A-1"), refs(blocked.overdue))
        assertEquals(listOf("A-2"), refs(blocked.due))
    }

    @Test
    fun todaysWorkIsYoursWhenYouAreKnownAndTheBoardsWhenNot() {
        val cards = listOf(card("A-1", assignee = ME), card("A-2", assignee = ADITI))
        assertEquals(listOf("A-1"), refs(todaysWork(cards, BOARD, ME.id, today).due))
        assertEquals(listOf("A-1", "A-2"), refs(todaysWork(cards, BOARD, null, today).due))
        assertEquals(listOf("A-1"), refs(todaysWork(listOf(card("A-1")), emptyList(), ME.id, today).due))
    }

    @Test
    fun todaysWorkIsInOrderOfUrgency() {
        val due = todaysWork(
            listOf(card("A-1", priority = TaskPriority.p3), card("A-2", priority = TaskPriority.p0), card("A-3", priority = TaskPriority.p2), card("A-4", priority = TaskPriority.p1)),
            BOARD, ME.id, today,
        ).due
        assertEquals(listOf("A-2", "A-4", "A-3", "A-1"), refs(due))
        val overdue = todaysWork(
            listOf(card("A-1", due = "2026-09-07", priority = TaskPriority.p0), card("A-2", due = "2026-08-18"), card("A-3", due = "2026-09-02", priority = TaskPriority.p1)),
            BOARD, ME.id, today,
        ).overdue
        assertEquals(listOf("A-2", "A-3", "A-1"), refs(overdue))
        val tie = todaysWork(listOf(card("A-1", due = "2026-09-05", priority = TaskPriority.p2), card("A-2", due = "2026-09-05", priority = TaskPriority.p0)), BOARD, ME.id, today)
        assertEquals(listOf("A-2", "A-1"), refs(tie.overdue))
        val same = listOf(card("A-1"), card("A-2"), card("A-3"))
        assertEquals(refs(todaysWork(same, BOARD, ME.id, today).due), refs(todaysWork(same.reversed(), BOARD, ME.id, today).due))
    }

    @Test
    fun theCardsOfferedAreDueOrLateFirstThenColumnsNearestDone() {
        val late = card("ATL-1", due = "2026-09-01", column = TO_DO.id)
        val doing = card("ATL-2", due = null, column = DOING.id)
        val todo = card("ATL-3", due = null, column = TO_DO.id)
        val groups = openCardGroups(listOf(todo, doing, late), BOARD, ME.id, mine = false, query = "", today = today)
        assertEquals(
            listOf("Due or late" to listOf("ATL-1"), "In progress" to listOf("ATL-2"), "To do" to listOf("ATL-3")),
            groups.map { it.name to refs(it.tasks) },
        )
        assertEquals(listOf(true, false, false), groups.map { it.late })
    }

    @Test
    fun theCardsOfferedLeaveOutFinishedCancelledAndStoredButKeepHeld() {
        val groups = openCardGroups(
            listOf(
                card("ATL-1", due = null, column = DONE.id),
                card("ATL-2", due = null, status = TaskStatus.cancelled),
                card("ATL-3", due = null, column = null),
                card("ATL-4", due = null, status = TaskStatus.hold),
            ),
            BOARD, ME.id, mine = false, query = "", today = today,
        )
        assertEquals(listOf("ATL-4"), groups.flatMap { refs(it.tasks) })
    }

    @Test
    fun theCardsOfferedNarrowToYoursAndToTheSearch() {
        val mine = card("ATL-1", due = null, assignee = ME)
        val theirs = card("ATL-2", due = null, assignee = ADITI)
        assertEquals(listOf(mine), openCardGroups(listOf(mine, theirs), BOARD, ME.id, mine = true, query = "", today = today).flatMap { it.tasks })
        assertEquals(listOf(theirs), openCardGroups(listOf(mine, theirs), BOARD, ME.id, mine = false, query = "atl-2", today = today).flatMap { it.tasks })
        assertEquals(2, cardCount(openCardGroups(listOf(mine, theirs), BOARD, ME.id, mine = false, query = "", today = today)))
    }

    @Test
    fun aLateCardOfYoursOffThePlanIsNamed() {
        val late = card("ATL-1", due = "2026-09-01", assignee = ME)
        assertEquals(late, lateAndUnplanned(listOf(late), BOARD, ME.id, today, emptySet()))
        assertNull(lateAndUnplanned(listOf(late), BOARD, ME.id, today, setOf(late.id)))
        assertEquals("ATL-1 is 7 days late", lateSaid(late, today))
        assertEquals("ATL-1 is a day late", lateSaid(card("ATL-1", due = "2026-09-07"), today))
    }

    @Test
    fun datesAreSaidAsTheWebSaysThem() {
        val friday = LocalDate.of(2026, 10, 16)
        assertEquals("Fri 16 Oct", dayShort(friday))
        assertEquals("Friday 16 October", dayLong(friday))
        assertEquals("Fri", weekdayShort(friday))
        assertEquals("Friday", weekdayName(friday))
        assertEquals("16 Oct", formatDue(friday))
        assertEquals(LocalDate.of(2026, 10, 19), nextWorkingDay(friday))
        assertEquals(LocalDate.of(2026, 10, 19), nextWorkingDay(LocalDate.of(2026, 10, 17)))
        assertEquals(LocalDate.of(2026, 10, 20), nextWorkingDay(LocalDate.of(2026, 10, 19)))
        assertEquals(LocalDate.of(2026, 10, 20), firstOtherDay(friday))
        assertEquals(LocalDate.of(2026, 10, 16), apiDate("2026-10-16T09:00:00Z"))
        assertNull(apiDate("not a date"))
    }

    @Test
    fun theDueMarkOnACardToPick() {
        // en-GB says "Sept" (CLDR, and so the browser's toLocaleDateString too).
        assertEquals(PickDue("1 Sept, late", DueTone.Late), pickDueOf(card("A", due = "2026-09-01"), today))
        assertEquals(PickDue("Today", DueTone.Today), pickDueOf(card("A"), today))
        assertEquals(PickDue("23 Sept", DueTone.Plain), pickDueOf(card("A", due = "2026-09-23"), today))
        assertNull(pickDueOf(card("A", due = null), today))
    }

    @Test
    fun aTodoSaysWhereItCameFromWhereItWentOrThatItWasLeft() {
        assertEquals(listOf("from Thu"), todoMarks(todo("x", carriedFrom = "2026-10-15")))
        assertEquals(listOf("→ Mon 19 Oct"), todoMarks(todo("x", state = TodoState.moved, movedTo = "2026-10-19")))
        assertEquals(listOf("from Thu", "not done"), todoMarks(todo("x", state = TodoState.not_done, carriedFrom = "2026-10-15")))
        assertEquals(listOf("sub-task"), todoMarks(todo("x", source = TodoRead.SourceKind.subtask)))
        assertEquals(listOf("stage"), todoMarks(todo("x", source = TodoRead.SourceKind.stage)))
        assertEquals(listOf("on the card"), todoMarks(todo("x", source = TodoRead.SourceKind.checklist)))
        assertEquals(emptyList<String>(), todoMarks(todo("x")))
        assertEquals("Ticking this finishes the sub-task on the card", onTheCard(todo("x", source = TodoRead.SourceKind.subtask)))
        assertNull(onTheCard(todo("x")))
    }

    @Test
    fun thePlanIsCountedAndTitled() {
        val friday = LocalDate.of(2026, 10, 16)
        assertNull(planNote(emptyList()))
        assertEquals("1 card, 1 to-do", planNote(listOf(pickOf("A-1", todo("a")))))
        assertEquals("2 cards, 3 to-dos", planNote(listOf(pickOf("A-1", todo("a"), todo("b")), pickOf("A-2", todo("c")))))
        assertEquals("Plan for today", planTitle(friday, friday))
        assertEquals("Plan for Saturday 17 October", planTitle(friday.plusDays(1), friday))
    }

    @Test
    fun wrappingUpTicksEveryOpenTodoAndSaysWhatWillHappen() {
        val friday = LocalDate.of(2026, 10, 16)
        val a = todo("a")
        val b = todo("b")
        val c = todo("c")
        val picks = listOf(
            pickOf("A-1", a, todo("done", state = TodoState.done)),
            pickOf("A-2", todo("gone", state = TodoState.moved, movedTo = "2026-10-19")),
            pickOf("A-3", b, c),
        )
        val open = openByCard(picks)
        assertEquals(listOf("A-1", "A-3"), open.map { it.first.task.reference })

        val toDay = wrapTargetDay(friday, WrapTarget.NextWorkingDay, firstOtherDay(friday))
        val everything = WrapUpPlan(friday, toDay, open.flatMap { (_, todos) -> todos.map { it.id } }, kept = emptySet())
        assertEquals(LocalDate.of(2026, 10, 19), toDay)
        assertEquals(listOf(a.id, b.id, c.id), everything.moving)
        assertTrue(everything.allTicked)
        assertEquals("Move 3 to Mon", everything.confirm)
        assertEquals("→ Mon", everything.movingMark)
        assertEquals("3 to-dos are still open. Ticked ones move on; the rest stay on Friday as not done.", everything.lead)

        val some = everything.copy(kept = setOf(b.id))
        assertEquals(listOf(a.id, c.id), some.moving)
        assertEquals(1, some.staying)
        assertFalse(some.allTicked)

        val none = everything.copy(kept = setOf(a.id, b.id, c.id))
        assertEquals("Leave all as not done", none.confirm)
        assertTrue(none.moving.isEmpty())

        assertFalse(everything.copy(toDay = friday).canConfirm)
        assertEquals(LocalDate.of(2026, 10, 22), wrapTargetDay(friday, WrapTarget.AnotherDay, LocalDate.of(2026, 10, 22)))
    }

    @Test
    fun theResultSaysWhatTheServerDid() {
        val friday = LocalDate.of(2026, 10, 16)
        val monday = LocalDate.of(2026, 10, 19)
        assertEquals("2 to-dos moved to Monday 19 October. 1 to-do stayed on Friday as not done.", wrapResultText(friday, monday, 2, 1))
        assertEquals("1 to-do moved to Monday 19 October.", wrapResultText(friday, monday, 1, 0))
        assertEquals("Nothing moved on. 2 to-dos stayed on Friday as not done.", wrapResultText(friday, monday, 0, 2))
    }
}
