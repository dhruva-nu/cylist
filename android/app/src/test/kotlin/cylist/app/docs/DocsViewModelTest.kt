package cylist.app.docs

import cylist.api.generated.models.DocAnswer
import cylist.app.docs.markdown.Block
import cylist.app.session.Session
import cylist.app.session.anAccount
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
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
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.Collections
import java.util.UUID

/** The Docs tab against a fake server answering the three endpoints the web Docs page calls. */
@OptIn(ExperimentalCoroutinesApi::class)
class DocsViewModelTest {
    private val dispatcher: TestDispatcher = StandardTestDispatcher()
    private val server = MockWebServer()
    private val requests: MutableList<String> = Collections.synchronizedList(mutableListOf())
    private val bodies: MutableList<String> = Collections.synchronizedList(mutableListOf())
    private var refusals = 0
    private var askStatus = 200
    private lateinit var model: DocsViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val path = request.url.encodedPath
                requests += "${request.method} $path"
                bodies += request.body?.utf8().orEmpty()
                return when {
                    path == "/api/v1/projects/ATL/docs" -> json(TREE)
                    path == "/api/v1/projects/ATL/docs/ask" -> if (askStatus == 200) json(ANSWER) else json("{}", askStatus)
                    path == "/api/v1/docs/$PAGING" -> json(DOC)
                    path.startsWith("/api/v1/docs/") -> json("""{"detail":"No such doc."}""", 404)
                    else -> json("{}", 404)
                }
            }
        }
        server.start()
        val account = anAccount.copy(server = server.url("/").toString().trimEnd('/'))
        model = DocsViewModel(Session(account, OkHttpClient()) { refusals++ }, io = dispatcher)
    }

    @After
    fun tearDown() {
        server.close()
        Dispatchers.resetMain()
    }

    private val state get() = model.state.value

    @Test
    fun aProjectsTreeIsLoadedWhenItIsShown() = runTest(dispatcher) {
        model.showProject("ATL")
        assertTrue(state.treeLoading)
        advanceUntilIdle()

        assertEquals(listOf("GET /api/v1/projects/ATL/docs"), requests)
        assertEquals(2, state.tree?.docCount)
        assertEquals(listOf("Engineering", "Product"), state.tree?.sections?.map { it.label })
        assertEquals(DocsPage.Tree, state.page)
    }

    @Test
    fun aDocOpenedFromTheTreeIsFetchedParsedAndBackGoesToTheTree() = runTest(dispatcher) {
        model.showProject("ATL")
        model.openFromTree(PAGING)
        advanceUntilIdle()

        assertTrue("GET /api/v1/docs/$PAGING" in requests)
        assertEquals(DocsPage.Reading(PAGING, fromAnswer = false), state.page)
        val reading = checkNotNull(state.reading)
        assertEquals("Device list paging", reading.doc.title)
        assertTrue(reading.markdown.blocks.any { it is Block.Code })
        assertEquals(listOf("The cursor" to 0, "Breaking ties" to 2), reading.markdown.contents)

        assertTrue(model.back())
        assertEquals(DocsPage.Tree, state.page)
        assertFalse("Back at the tree leaves the tab to the system", model.back())
    }

    @Test
    fun aQuestionIsSentTrimmedAsTheWebSendsIt() = runTest(dispatcher) {
        model.showProject("ATL")
        advanceUntilIdle()
        model.setDraft("  How does paging break ties?  ")
        model.ask()
        assertEquals("", state.draft)
        assertEquals(DocsPage.Answer("How does paging break ties?"), state.page)
        assertTrue(checkNotNull(state.asked).pending)
        advanceUntilIdle()

        val index = requests.indexOf("POST /api/v1/projects/ATL/docs/ask")
        assertTrue(index >= 0)
        assertEquals("""{"question":"How does paging break ties?"}""", bodies[index])
        val answer = checkNotNull(state.asked?.answer)
        assertEquals(DocAnswer.Status.ok, answer.status)
        assertEquals("Breaking ties", answer.found?.section)
    }

    @Test
    fun aDocOpenedFromTheAnswerGoesBackToTheAnswerAndKnowsWhichSectionToMark() = runTest(dispatcher) {
        model.showProject("ATL")
        model.setDraft("How does paging break ties?")
        model.ask()
        advanceUntilIdle()

        model.openFromAnswer(PAGING)
        advanceUntilIdle()
        assertEquals(
            listOf(DocsPage.Tree, DocsPage.Answer("How does paging break ties?"), DocsPage.Reading(PAGING, fromAnswer = true)),
            state.stack,
        )
        val section = checkNotNull(focusSection(state.asked?.answer, PAGING))
        val mark = checkNotNull(state.reading?.markdown?.sectionMark(section))
        assertEquals(2, mark.topIndex)

        model.back()
        assertEquals(DocsPage.Answer("How does paging break ties?"), state.page)
        assertEquals(1, requests.count { it.endsWith("/docs/ask") })
    }

    @Test
    fun theSameQuestionAgainIsNotAskedAgain() = runTest(dispatcher) {
        model.showProject("ATL")
        repeat(2) {
            model.setDraft("How does paging break ties?")
            model.ask()
            advanceUntilIdle()
        }
        assertEquals(1, requests.count { it.endsWith("/docs/ask") })
    }

    @Test
    fun aBlankQuestionIsNotAsked() = runTest(dispatcher) {
        model.showProject("ATL")
        model.setDraft("   ")
        model.ask()
        advanceUntilIdle()
        assertEquals(DocsPage.Tree, state.page)
        assertNull(state.asked)
    }

    @Test
    fun aFailedAskSaysSoAndCanBeAskedAgain() = runTest(dispatcher) {
        model.showProject("ATL")
        askStatus = 500
        model.setDraft("How does paging break ties?")
        model.ask()
        advanceUntilIdle()
        assertTrue(state.asked?.error != null)

        askStatus = 200
        model.retryAsk()
        advanceUntilIdle()
        assertEquals(DocAnswer.Status.ok, state.asked?.answer?.status)
    }

    @Test
    fun aDocThatIsGoneSaysSo() = runTest(dispatcher) {
        model.showProject("ATL")
        model.openFromTree(UUID.randomUUID())
        advanceUntilIdle()
        assertNull(state.reading)
        assertEquals("This doc is not there any more.", state.readingError)
    }

    @Test
    fun aRefusedTokenOnTheAskSignsTheAppOut() = runTest(dispatcher) {
        model.showProject("ATL")
        askStatus = 401
        model.setDraft("anything")
        model.ask()
        advanceUntilIdle()
        assertEquals(1, refusals)
    }

    @Test
    fun anotherProjectStartsAtItsOwnTree() = runTest(dispatcher) {
        model.showProject("ATL")
        model.openFromTree(PAGING)
        advanceUntilIdle()

        model.showProject("HWL")
        assertEquals(DocsPage.Tree, state.page)
        assertNull(state.tree)
        assertNull(state.reading)
        assertEquals("HWL", state.projectKey)
    }

    private companion object {
        const val PAGING_ID = "01a150ad-a24c-70c2-8c75-72d6d8634cad"
        val PAGING: UUID = UUID.fromString(PAGING_ID)
        const val TOPIC = "0190f0c0-0000-7000-8000-000000000001"
        const val PROJECT = "0190f0c0-0000-7000-8000-000000000002"
        const val PERSON = """{"id":"0190f0c0-0000-7000-8000-000000000003","name":"Aditi K","kind":"team","title":"","responsibilities":"",
            "email":null,"colour":"#3B6FC2","archived_at":null,"created_at":"2026-10-01T00:00:00Z","is_agent":false,"has_account":true,"invite_is_pending":false}"""
        const val LISTING = """{"id":"$PAGING_ID","topic_id":"$TOPIC","title":"Device list paging","position":0,"author":$PERSON,
            "created_at":"2026-10-14T10:00:00Z","updated_at":"2026-10-14T10:00:00Z"}"""
        const val TREE = """{"doc_count":2,"sections":[
            {"section":"engineering","label":"Engineering","topics":[{"id":"$TOPIC","project_id":"$PROJECT","section":"engineering",
              "name":"APIs","position":0,"doc_count":2,"docs":[$LISTING]}]},
            {"section":"product","label":"Product","topics":[]}]}"""
        const val BODY = "## The cursor\\n\\nEach page ends with a `next` cursor.\\n\\n## Breaking ties\\n\\n```sql\\nORDER BY updated_at, id\\n```\\n"
        const val DOC = """{"id":"$PAGING_ID","topic_id":"$TOPIC","title":"Device list paging","position":0,"author":$PERSON,
            "created_at":"2026-10-14T10:00:00Z","updated_at":"2026-10-14T10:00:00Z","project_id":"$PROJECT","section":"engineering",
            "topic_name":"APIs","body":"$BODY"}"""
        const val ANSWER = """{"question":"How does paging break ties?","status":"ok","threshold":0.7,
            "found":{"doc_id":"$PAGING_ID","path":"Engineering / APIs / Device list paging","section":"Breaking ties",
              "text":"```sql\\nORDER BY updated_at, id\\n```","whole_doc":false,"relevance":0.98},
            "also":null,"alternatives":[],"reason":null}"""

        fun json(body: String, code: Int = 200): MockResponse =
            MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()
    }
}
