package cylist.app.task

import cylist.api.ApiFailure
import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.generated.infrastructure.ClientError
import cylist.api.generated.infrastructure.ClientException
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentFeedKind
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionState
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.MemberRead
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TemplateRead
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.util.UUID

@OptIn(ExperimentalCoroutinesApi::class)
class TaskDetailViewModelTest {
    private val dispatcher = StandardTestDispatcher()
    private val source = FakeTaskSource()

    @Before
    fun setUp() = Dispatchers.setMain(dispatcher)

    @After
    fun tearDown() = Dispatchers.resetMain()

    private fun model() = TaskDetailViewModel(source, "ATL", "ATL-1", io = dispatcher)

    @Test
    fun opensWithTheCardItsBoardAndWhoAddedIt() = runTest(dispatcher) {
        val model = model()
        assertTrue(model.state.value.loading)
        advanceUntilIdle()

        val state = model.state.value
        assertEquals(source.card, state.task)
        assertEquals(board, state.columns)
        assertEquals("Aditi K", state.reporter)
        assertEquals(false, state.loading)
        // No agent on the card, so nobody asked for a feed.
        assertEquals(0, source.feedCalls)
    }

    @Test
    fun aCardThatWillNotLoadSaysWhy() = runTest(dispatcher) {
        source.taskFails = IOException("no route")
        val model = model()
        advanceUntilIdle()
        assertEquals("Can't reach the server.", model.state.value.error)
        assertNull(model.state.value.task)
    }

    @Test
    fun theFeedIsFetchedWhileAnAgentIsOnTheCard() = runTest(dispatcher) {
        source.card = detail(sessions = listOf(session()))
        val model = model()
        advanceUntilIdle()
        assertEquals(source.feed, model.state.value.feed)
    }

    @Test
    fun aMoveIsSentAtTheFootOfTheColumnAndTheCardRefetched() = runTest(dispatcher) {
        val model = model()
        advanceUntilIdle()
        model.move(board[2].id)
        assertTrue(model.state.value.moving)
        advanceUntilIdle()

        assertEquals(listOf(Triple(board[2].id, board[2].taskCount, null as String?)), source.moves)
        assertEquals(false, model.state.value.moving)
        assertEquals(board[2].id, model.state.value.task?.columnId)
    }

    @Test
    fun aMoveToDoneOverOpenWorkIsRefusedWithoutAsking() = runTest(dispatcher) {
        source.card = detail(checklist = listOf(item("Write tests")))
        val model = model()
        advanceUntilIdle()
        model.move(board[3].id, "Done")
        advanceUntilIdle()

        assertTrue(source.moves.isEmpty())
        assertEquals(
            "ATL-1 still has 1 unfinished sub-task: Write tests. Finish or cancel each of them before moving this card to Done.",
            model.state.value.notice,
        )
    }

    @Test
    fun theServersRefusalIsShownInItsOwnWords() = runTest(dispatcher) {
        source.moveFails = refusal(422, "unprocessable", "ATL-1 is still on \"Build\" in In progress.")
        val model = model()
        advanceUntilIdle()
        model.move(board[2].id)
        advanceUntilIdle()
        assertEquals("ATL-1 is still on \"Build\" in In progress.", model.state.value.notice)
        model.dismissNotice()
        assertNull(model.state.value.notice)
    }

    @Test
    fun reopeningAFinishedCardWaitsForAYes() = runTest(dispatcher) {
        source.card = detail(columnId = board[3].id, outcome = "Done", finishedAt = "2026-10-18T10:00:00Z")
        val model = model()
        advanceUntilIdle()

        model.move(board[1].id)
        assertEquals(PendingMove(board[1].id, null, "Done", "In progress"), model.state.value.pendingMove)
        assertTrue(source.moves.isEmpty())

        model.cancelMove()
        assertNull(model.state.value.pendingMove)
        model.move(board[1].id)
        model.confirmMove()
        advanceUntilIdle()
        assertEquals(board[1].id, source.moves.single().first)
    }

    @Test
    fun aTapTicksAChecklistItemAndASecondTapWhileSavingIsIgnored() = runTest(dispatcher) {
        val box = item("Write tests")
        source.card = detail(checklist = listOf(box))
        val model = model()
        advanceUntilIdle()

        model.toggle(box)
        model.toggle(box)
        assertTrue(box.id in model.state.value.busy)
        advanceUntilIdle()

        assertEquals(listOf(box.id to ChecklistState.done), source.ticks)
        assertTrue(model.state.value.busy.isEmpty())
        assertEquals(ChecklistState.done, model.state.value.task?.checklist?.single()?.state)
    }

    @Test
    fun aCancelledItemIsNotTicked() = runTest(dispatcher) {
        val dropped = item("Dropped", ChecklistState.cancelled)
        source.card = detail(checklist = listOf(dropped))
        val model = model()
        advanceUntilIdle()
        model.toggle(dropped)
        advanceUntilIdle()
        assertTrue(source.ticks.isEmpty())
    }

    @Test
    fun aSubTaskIsFinishedAndReopenedByATap() = runTest(dispatcher) {
        val open = subtask("ATL-1-1")
        val done = subtask("ATL-1-2", finishedAt = "2026-10-18T10:00:00Z")
        source.card = detail(subtasks = listOf(open, done))
        val model = model()
        advanceUntilIdle()
        model.toggle(open)
        model.toggle(done)
        advanceUntilIdle()
        assertEquals(listOf("ATL-1-1" to true, "ATL-1-2" to false), source.finishes)
    }

    @Test
    fun aCommentEmptiesTheBoxOnlyOnceTheServerHasIt() = runTest(dispatcher) {
        val model = model()
        advanceUntilIdle()

        model.setDraft("   ")
        model.sendComment()
        advanceUntilIdle()
        assertTrue("nothing to say is not sent", source.comments.isEmpty())

        source.commentFails = IOException("dropped")
        model.setDraft(" Reproduces on staging. ")
        model.sendComment()
        advanceUntilIdle()
        assertEquals(" Reproduces on staging. ", model.state.value.draft)
        assertEquals("Can't reach the server.", model.state.value.notice)

        source.commentFails = null
        model.sendComment()
        advanceUntilIdle()
        assertEquals(listOf("Reproduces on staging."), source.comments)
        assertEquals("in the signed-in person's name", listOf(source.me.id), source.authors)
        assertEquals("", model.state.value.draft)
        assertEquals(false, model.state.value.sending)
    }

    @Test
    fun anAnswerIsSentAndTheCardRefetched() = runTest(dispatcher) {
        val asking = prompt()
        source.card = detail(sessions = listOf(session(AgentSessionState.waiting, prompt = asking)))
        val model = model()
        advanceUntilIdle()
        val loads = source.taskCalls

        model.answer(asking, AgentPromptAnswer(key = "40"))
        assertTrue(asking.id in model.state.value.busy)
        advanceUntilIdle()

        assertEquals(listOf(asking.id to AgentPromptAnswer(key = "40")), source.answers)
        assertEquals(loads + 1, source.taskCalls)
        assertTrue(model.state.value.busy.isEmpty())
        assertNull(model.state.value.notice)
    }

    @Test
    fun anAnswerSomebodyElseGotInFirstSaysWhoAndWhat() = runTest(dispatcher) {
        val asking = prompt(AgentPromptKind.permission)
        source.card = detail(sessions = listOf(session(AgentSessionState.waiting, prompt = asking)))
        source.answerFails = refusal(409, "conflict", "Already answered by Aditi K: Allow once.")
        val model = model()
        advanceUntilIdle()
        model.answer(asking, AgentPromptAnswer(key = "deny"))
        advanceUntilIdle()
        assertEquals("Already answered by Aditi K: Allow once.", model.state.value.notice)
    }

    @Test
    fun anAnswerThatNeverArrivedSaysSo() = runTest(dispatcher) {
        val asking = prompt()
        source.card = detail(sessions = listOf(session(AgentSessionState.waiting, prompt = asking)))
        source.answerFails = IOException("reset")
        val model = model()
        advanceUntilIdle()
        model.answer(asking, AgentPromptAnswer(text = "40"))
        advanceUntilIdle()
        assertEquals("The answer did not reach the agent: Can't reach the server.", model.state.value.notice)
    }

    @Test
    fun theBoardsDoorbellsRefetchTheCardAndItsFeedDebounced() = runTest(dispatcher) {
        source.card = detail(sessions = listOf(session()))
        val model = model()
        advanceUntilIdle()
        model.connect()
        val listener = source.listener!!
        assertTrue(source.watching)
        val tasks = source.taskCalls
        val feeds = source.feedCalls

        listener.onConnectedChanged(true)
        assertTrue(model.state.value.live)
        repeat(3) { listener.onEvent(BoardEvent.Changed("2026-10-19T09:00:00Z")) }
        listener.onEvent(BoardEvent.AgentFeed(source.card.id.toString(), "2026-10-19T09:00:00Z"))
        listener.onEvent(BoardEvent.AgentFeed(UUID.randomUUID().toString(), "2026-10-19T09:00:00Z"))
        advanceTimeBy(100)
        assertEquals(tasks, source.taskCalls)
        advanceUntilIdle()

        // Three doorbells, one fetch of the card (which brings the feed); one more of the feed for its own line.
        assertEquals(tasks + 1, source.taskCalls)
        assertEquals(feeds + 2, source.feedCalls)

        model.disconnect()
        assertEquals(false, source.watching)
        assertEquals(false, model.state.value.live)
    }

    @Test
    fun aFetchCancelledByANewerOneIsNotAFailure() = runTest(dispatcher) {
        val model = model()
        advanceUntilIdle()
        // What `refresh` cancelling the fetch it replaces looks like from inside that fetch.
        source.taskFails = kotlinx.coroutines.CancellationException("replaced by a newer refresh")
        model.refresh()
        advanceUntilIdle()
        assertNull(model.state.value.notice)
        assertNull(model.state.value.error)
    }

    @Test
    fun aFailureReadsAsTheServersSentenceOrAPlainOne() {
        assertEquals("Nope.", TaskDetailViewModel.describe(refusal(422, "unprocessable", "Nope.")))
        assertEquals("The server refused that (502).", TaskDetailViewModel.describe(ClientException("Client error : 502", 502, ClientError<Any>(body = "<html/>", statusCode = 502))))
        assertEquals("Can't reach the server.", TaskDetailViewModel.describe(java.net.UnknownHostException("cylist.in")))
    }

    private fun refusal(status: Int, code: String, message: String) = ClientException(
        "Client error : $status",
        status,
        ClientError<Any>(body = """{"error":{"code":"$code","message":${kotlinx.serialization.json.JsonPrimitive(message)}}}""", statusCode = status),
    ).also { check(ApiFailure.of(it)?.message == message) }
}

/** A [TaskSource] that answers from what each test sets, and records what it was asked. */
class FakeTaskSource : TaskSource {
    var card: TaskDetail = detail()
    var taskFails: Exception? = null
    var moveFails: Exception? = null
    var commentFails: Exception? = null
    var answerFails: Exception? = null
    val feed = listOf(
        AgentFeedEntryRead(UUID.randomUUID(), UUID.randomUUID(), "Claude Code", "2026-10-19T09:01:00Z", AgentFeedKind.run, "Bash", "pytest tests/devices"),
    )
    val moves = mutableListOf<Triple<UUID, Int, String?>>()
    val ticks = mutableListOf<Pair<UUID, ChecklistState>>()
    val finishes = mutableListOf<Pair<String, Boolean>>()
    val comments = mutableListOf<String>()
    val authors = mutableListOf<UUID?>()
    val answers = mutableListOf<Pair<UUID, AgentPromptAnswer>>()
    var taskCalls = 0
    var feedCalls = 0
    var listener: BoardSocket.Listener? = null
    var watching = false

    val me = person("Dhruva N")

    override fun me(): PersonRead = me

    override fun task(ref: String): TaskDetail {
        taskCalls++
        taskFails?.let { throw it }
        return card
    }

    override fun columns(projectKey: String): List<ColumnRead> = board

    override fun templates(projectKey: String): List<TemplateRead> = emptyList()

    override fun members(projectKey: String): List<MemberRead> = emptyList()

    override fun reporter(ref: String): String = "Aditi K"

    override fun feed(ref: String): List<AgentFeedEntryRead> {
        feedCalls++
        return feed
    }

    override fun move(ref: String, columnId: UUID, position: Int, outcome: String?): TaskDetail {
        moveFails?.let { throw it }
        moves += Triple(columnId, position, outcome)
        card = card.copy(columnId = columnId, outcome = outcome)
        return card
    }

    override fun setChecklistState(itemId: UUID, state: ChecklistState) {
        ticks += itemId to state
        card = card.copy(checklist = card.checklist.map { if (it.id == itemId) it.copy(state = state) else it })
    }

    override fun finishSubtask(ref: String, finished: Boolean) {
        finishes += ref to finished
    }

    override fun comment(ref: String, body: String, authorId: UUID?) {
        commentFails?.let { throw it }
        comments += body
        authors += authorId
    }

    override fun answer(ref: String, promptId: UUID, answer: AgentPromptAnswer): AgentPromptRead {
        answerFails?.let { throw it }
        answers += promptId to answer
        return prompt(status = AgentPromptStatus.answered)
    }

    override fun watch(projectKey: String, listener: BoardSocket.Listener): TaskSource.Watch {
        this.listener = listener
        return object : TaskSource.Watch {
            override fun start() {
                watching = true
            }

            override fun stop() {
                watching = false
            }
        }
    }
}
