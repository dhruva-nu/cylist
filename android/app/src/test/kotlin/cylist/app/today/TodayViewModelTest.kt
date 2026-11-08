package cylist.app.today

import cylist.api.generated.models.TodoState
import cylist.app.session.Account
import cylist.app.session.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.TestScope
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import mockwebserver3.Dispatcher
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import mockwebserver3.RecordedRequest
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.time.Clock
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.Collections

/**
 * The Today view model against a fake server: what it asks the `/today`
 * routes for, and what it sends them, is what the web planner sends.
 */
@OptIn(ExperimentalCoroutinesApi::class)
class TodayViewModelTest {
    private val dispatcher: TestDispatcher = StandardTestDispatcher()
    private val server = MockWebServer()
    private val requests: MutableList<Seen> = Collections.synchronizedList(mutableListOf())

    /** Friday 16 October, mid-morning in Kolkata. */
    private val clock = Clock.fixed(Instant.parse("2026-10-16T04:30:00Z"), ZoneId.of("Asia/Kolkata"))
    private val friday = LocalDate.of(2026, 10, 16)

    /** What the fake server answers for a patch of a to-do; a test may make it refuse. */
    private var patchAnswer: MockResponse = json("""{"id":"$TODO_A","text":"Ask @Aditi K","state":"done","done_at":null,"moved_to":null,"carried_from":null,"source_kind":null,"created_at":"2026-10-16T04:00:00Z"}""")

    private data class Seen(val method: String, val path: String, val query: Map<String, String?>, val body: String)

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val url = request.url
                requests += Seen(request.method, url.encodedPath, url.queryParameterNames.associateWith { url.queryParameter(it) }, request.body?.utf8().orEmpty())
                return answer(request.method, url.encodedPath, url.queryParameter("date"))
            }
        }
        server.start()
    }

    @After
    fun tearDown() {
        server.close()
        Dispatchers.resetMain()
    }

    private fun answer(method: String, path: String, date: String?): MockResponse = when {
        path == "/api/v1/me" -> json("""{"token_id":"$TOKEN","label":"Pixel","channel":"api","scopes":["read","write"],"person":${personJson("Dhruva N", ME_ID)}}""")
        path == "/api/v1/people" -> json("""{"items":[${personJson("Dhruva N", ME_ID)},${personJson("Aditi K", "01a150aa-6ebd-70cf-bfe4-00f8fb3fae01")}],"next":null}""")
        path.endsWith("/items") -> json("""{"items":[],"next":null}""")
        path.endsWith("/tasks") -> json("""{"columns":[]}""")
        path.endsWith("/columns") -> json("""{"columns":[]}""")
        path.endsWith("/today") && method == "GET" -> json(planJson(date ?: "2026-10-16"))
        path.endsWith("/today/picks") -> json("""{"id":"$PICK","day":"2026-10-16","task":{"id":"$CARD","reference":"ATL-3","title":"Card","column":"In progress"},"todos":[]}""", 201)
        path.startsWith("/api/v1/today/todos/") && method == "PATCH" -> patchAnswer
        path.endsWith("/reports/day") -> json("""{"project_key":"ATL","project_name":"Atlas","day":"$date","timezone":"Asia/Kolkata","starts_at":"2026-10-15T18:30:00Z","ends_at":"2026-10-16T18:30:00Z","entry_count":0,"finished":[],"tasks":[],"elsewhere":[],"todos":[],"headline":"A quiet day.","markdown":"A quiet day."}""")
        path.endsWith("/today/wrap-up") -> json("""{"day":"2026-10-16","to_day":"2026-10-19","moved":1,"not_done":0}""")
        else -> MockResponse.Builder().code(404).body("""{"error":{"code":"not_found","message":"no route"}}""").build()
    }

    /** Friday's plan holds one open to-do; any other day's is empty with two open elsewhere counted. */
    private fun planJson(date: String): String {
        val picks = if (date == "2026-10-16") {
            """[{"id":"$PICK","day":"$date","task":{"id":"$CARD","reference":"ATL-3","title":"Card","column":"In progress"},
               "todos":[{"id":"$TODO_A","text":"Ask @Aditi K","state":"open","done_at":null,"moved_to":null,"carried_from":null,"source_kind":null,"created_at":"2026-10-16T04:00:00Z"}]}]"""
        } else {
            "[]"
        }
        val open = if (date == "2026-10-16") 1 else 0
        return """{"project_key":"ATL","day":"$date","timezone":"Asia/Kolkata","picks":$picks,"free_todos":[],"open_count":$open,"done_count":0}"""
    }

    private fun model(): TodayViewModel {
        val account = Account(server.url("/").toString().trimEnd('/'), "cyl_phone", TOKEN, "Pixel (Android)", "Dhruva N")
        val session = Session(account, OkHttpClient()) {}
        return TodayViewModel(session, clock, io = dispatcher)
    }

    private fun TestScope.opened(): TodayViewModel = model().also {
        it.setProject("ATL")
        advanceUntilIdle()
    }

    private fun seen(method: String, suffix: String) = requests.filter { it.method == method && it.path.endsWith(suffix) }

    @Test
    fun readsTodaysPlanInThePhonesOwnZone() = runTest(dispatcher) {
        val model = opened()

        val get = seen("GET", "/projects/ATL/today").single()
        assertEquals(mapOf("date" to "2026-10-16", "timezone" to "Asia/Kolkata"), get.query)
        val state = model.state.value
        assertEquals(friday, state.day)
        assertEquals(listOf("ATL-3"), state.plan?.picks?.map { it.task.reference })
        assertEquals(1, state.todayOpen)
        assertEquals("Dhruva N", state.me?.name)
        assertEquals(listOf("Dhruva N", "Aditi K"), state.people.map { it.name })
    }

    @Test
    fun anotherDayIsReadAndTodaysCountIsKept() = runTest(dispatcher) {
        val model = opened()
        model.showDay(friday.plusDays(3))
        advanceUntilIdle()

        assertEquals(listOf("2026-10-16", "2026-10-19", "2026-10-16"), seen("GET", "/today").map { it.query["date"] })
        assertEquals(LocalDate.of(2026, 10, 19), model.state.value.day)
        assertTrue(model.state.value.plan!!.picks.isEmpty())
        assertEquals("the tab still counts today's open to-dos", 1, model.state.value.todayOpen)
    }

    @Test
    fun pickingPutsTheCardOnTheDayOnScreen() = runTest(dispatcher) {
        val model = opened()
        model.pick(card("ATL-3"))
        advanceUntilIdle()

        assertEquals("""{"task":"ATL-3","day":"2026-10-16"}""", seen("POST", "/projects/ATL/today/picks").single().body)
        assertEquals("the plan is read again after", 2, seen("GET", "/today").size)
    }

    @Test
    fun aTickShowsAtOnceAndSendsOnlyTheState() = runTest(dispatcher) {
        val model = opened()
        model.tick(TODO_A_ID, done = true)

        assertEquals(TodoState.done, model.state.value.plan!!.picks.single().todos.single().state)
        advanceUntilIdle()
        assertEquals("""{"text":null,"state":"done"}""", seen("PATCH", "/today/todos/$TODO_A").single().body)
    }

    @Test
    fun aRefusedTickSaysWhyOnItsRow() = runTest(dispatcher) {
        patchAnswer = json("""{"error":{"code":"forbidden","message":"Your role may not move cards out of Review.","details":{}}}""", 403)
        val model = opened()
        model.tick(TODO_A_ID, done = true)
        advanceUntilIdle()

        assertEquals("Your role may not move cards out of Review.", model.state.value.todoErrors[TODO_A_ID])
        assertEquals("the refetch puts the box back", TodoState.`open`, model.state.value.plan!!.picks.single().todos.single().state)
    }

    @Test
    fun wrappingUpSendsWhatTheWebSends() = runTest(dispatcher) {
        val model = opened()
        var result: Result<cylist.api.generated.models.WrapUpResult>? = null
        model.wrapUp(friday, LocalDate.of(2026, 10, 19), listOf(TODO_A_ID)) { result = it }
        advanceUntilIdle()

        assertEquals(
            """{"day":"2026-10-16","to_day":"2026-10-19","move":["$TODO_A"],"timezone":"Asia/Kolkata"}""",
            seen("POST", "/projects/ATL/today/wrap-up").single().body,
        )
        assertEquals(1, result!!.getOrThrow().moved)
        assertNull(model.state.value.error)
    }

    @Test
    fun theSummaryIsTheWebsDayReportInThePhonesZone() = runTest(dispatcher) {
        val model = opened()
        var result: Result<cylist.api.generated.models.DayReport>? = null
        model.report(friday) { result = it }
        advanceUntilIdle()

        assertEquals(mapOf("date" to "2026-10-16", "timezone" to "Asia/Kolkata"), seen("GET", "/projects/ATL/reports/day").single().query)
        assertEquals("A quiet day.", result!!.getOrThrow().headline)
    }

    private companion object {
        const val TOKEN = "0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b"
        const val ME_ID = "01a150aa-6ebd-73e6-b4c2-098c225f53ca"
        const val PICK = "01a150b0-0000-7000-8000-000000000001"
        const val CARD = "01a150b0-0000-7000-8000-000000000002"
        const val TODO_A = "01a150b0-0000-7000-8000-000000000003"
        val TODO_A_ID: java.util.UUID = java.util.UUID.fromString(TODO_A)

        fun json(body: String, code: Int = 200): MockResponse =
            MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

        fun personJson(name: String, id: String) =
            """{"id":"$id","name":"$name","kind":"team","title":"Tech lead","responsibilities":"","email":null,"colour":"#1D7D46","archived_at":null,"created_at":"2026-10-01T00:00:00Z","is_agent":false,"has_account":true,"invite_is_pending":false}"""
    }
}
