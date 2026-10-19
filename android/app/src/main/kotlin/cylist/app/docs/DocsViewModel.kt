package cylist.app.docs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.generated.apis.DocsApi
import cylist.api.generated.infrastructure.ClientException
import cylist.api.generated.models.DocAnswer
import cylist.api.generated.models.DocQuestion
import cylist.api.generated.models.DocRead
import cylist.api.generated.models.DocTree
import cylist.app.docs.markdown.MarkdownDoc
import cylist.app.session.Session
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import java.util.UUID
import java.util.concurrent.TimeUnit

/** Where the Docs tab is: the tree, a doc being read, or the answer to a question. */
sealed interface DocsPage {
    data object Tree : DocsPage

    /** [fromAnswer]: opened from the answer below it on the stack, so Back goes to the answer and its section is marked. */
    data class Reading(val docId: UUID, val fromAnswer: Boolean) : DocsPage

    data class Answer(val question: String) : DocsPage
}

/** One doc as fetched, parsed once rather than on every frame. */
data class OpenDoc(val doc: DocRead, val markdown: MarkdownDoc)

/** A question put to the docs, and what came back. */
data class Asked(val question: String, val answer: DocAnswer? = null, val error: String? = null) {
    val pending: Boolean get() = answer == null && error == null
}

data class DocsState(
    val projectKey: String? = null,
    val tree: DocTree? = null,
    val treeError: String? = null,
    /** Topics opened in the tree, by id. */
    val expanded: Set<UUID> = emptySet(),
    /** What is typed in the ask box. */
    val draft: String = "",
    /** The way back: the last page is the one showing. Never empty; the first is always the tree. */
    val stack: List<DocsPage> = listOf(DocsPage.Tree),
    val reading: OpenDoc? = null,
    val readingError: String? = null,
    val asked: Asked? = null,
) {
    val page: DocsPage get() = stack.last()
    val treeLoading: Boolean get() = tree == null && treeError == null && projectKey != null
}

/**
 * The Docs tab's data: a project's tree of docs, the doc open in the reader,
 * and the last question asked. Calls the same three endpoints the web Docs page
 * does (ProjectDocs.tsx): `GET /projects/{key}/docs`, `GET /docs/{id}` and
 * `POST /projects/{key}/docs/ask`, so a question asked here comes back with the
 * section the web would show.
 */
class DocsViewModel(
    private val session: Session,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val client = session.client
    private val _state = MutableStateFlow(DocsState())
    val state: StateFlow<DocsState> = _state.asStateFlow()

    private var treeJob: Job? = null
    private var docJob: Job? = null
    private var askJob: Job? = null

    /**
     * An answer is several jev calls, each allowed ten seconds on the server
     * (`CYLIST_JEV_TIMEOUT_SECONDS`), so the ask waits far longer than the
     * session's ten-second read timeout would let it. Same interceptors, so a
     * 401 still signs the app out.
     */
    private val askHttp: OkHttpClient by lazy {
        client.http.newBuilder().readTimeout(ASK_TIMEOUT_S, TimeUnit.SECONDS).callTimeout(ASK_TIMEOUT_S, TimeUnit.SECONDS).build()
    }

    /** Show [key]'s docs. The Docs tab follows the project the board is on. A new project starts at its tree. */
    fun showProject(key: String?) {
        if (key == _state.value.projectKey) return
        treeJob?.cancel()
        docJob?.cancel()
        askJob?.cancel()
        _state.value = DocsState(projectKey = key)
        if (key != null) refresh()
    }

    /** Refetch the tree, and the open doc with it: the screen calls this each time it comes on. */
    fun refresh() {
        val key = _state.value.projectKey ?: return
        treeJob?.cancel()
        treeJob = viewModelScope.launch {
            try {
                val tree = io { client.api(::DocsApi).getTreeApiV1ProjectsProjectRefDocsGet(key) }
                _state.update { if (it.projectKey == key) it.copy(tree = tree, treeError = null) else it }
            } catch (e: Exception) {
                _state.update { if (it.projectKey == key) it.copy(treeError = describe(e)) else it }
            }
        }
        (_state.value.page as? DocsPage.Reading)?.let { load(it.docId, keep = true) }
    }

    fun toggleTopic(id: UUID) = _state.update { it.copy(expanded = if (id in it.expanded) it.expanded - id else it.expanded + id) }

    fun setDraft(text: String) = _state.update { it.copy(draft = text) }

    /** Open a doc from the tree: the question, if there was one, is done with. */
    fun openFromTree(docId: UUID) {
        _state.update { it.copy(stack = listOf(DocsPage.Tree, DocsPage.Reading(docId, fromAnswer = false))) }
        load(docId, keep = false)
    }

    /** Open a doc from the answer, keeping the question to come back to. */
    fun openFromAnswer(docId: UUID) {
        _state.update { current ->
            val upToAnswer = current.stack.takeWhile { it !is DocsPage.Reading }
            current.copy(stack = upToAnswer + DocsPage.Reading(docId, fromAnswer = true))
        }
        load(docId, keep = false)
    }

    /** Ask the docs what is in the ask box. Nothing happens for a blank one. */
    fun ask() {
        val key = _state.value.projectKey ?: return
        val question = questionToAsk(_state.value.draft) ?: return
        _state.update { it.copy(draft = "", stack = listOf(DocsPage.Tree, DocsPage.Answer(question))) }
        val previous = _state.value.asked
        // The same question again is the answer already here, as the web keeps one for five minutes.
        if (previous?.question == question && previous.answer != null) return
        askJob?.cancel()
        _state.update { it.copy(asked = Asked(question)) }
        askJob = viewModelScope.launch {
            val asked = try {
                val answer = io {
                    client.api { base, _ -> DocsApi(base, askHttp) }
                        .askDocsApiV1ProjectsProjectRefDocsAskPost(key, DocQuestion(question))
                }
                Asked(question, answer = answer)
            } catch (e: Exception) {
                Asked(question, error = describe(e))
            }
            _state.update { if (it.projectKey == key && it.asked?.question == question) it.copy(asked = asked) else it }
        }
    }

    /** Ask the same question again, after it failed. */
    fun retryAsk() {
        val question = _state.value.asked?.question ?: return
        _state.update { it.copy(draft = question, asked = null) }
        ask()
    }

    /** Back one page. False at the tree, where Back leaves the tab to the system. */
    fun back(): Boolean {
        val stack = _state.value.stack
        if (stack.size <= 1) return false
        _state.update { it.copy(stack = stack.dropLast(1)) }
        return true
    }

    /** Back to the tree, whatever is open. */
    fun toTree() = _state.update { it.copy(stack = listOf(DocsPage.Tree)) }

    private fun load(docId: UUID, keep: Boolean) {
        docJob?.cancel()
        if (!keep || _state.value.reading?.doc?.id != docId) _state.update { it.copy(reading = null, readingError = null) }
        docJob = viewModelScope.launch {
            try {
                val open = io {
                    val doc = client.api(::DocsApi).getDocApiV1DocsDocIdGet(docId)
                    OpenDoc(doc, MarkdownDoc.parse(doc.body))
                }
                _state.update { if ((it.page as? DocsPage.Reading)?.docId == docId) it.copy(reading = open, readingError = null) else it }
            } catch (e: Exception) {
                _state.update { if ((it.page as? DocsPage.Reading)?.docId == docId) it.copy(readingError = describe(e)) else it }
            }
        }
    }

    private suspend fun <T> io(block: () -> T): T = withContext(io) { block() }

    private companion object {
        const val ASK_TIMEOUT_S = 60L

        fun describe(e: Exception): String = when {
            e is java.net.UnknownHostException || e is java.net.ConnectException -> "Can't reach the server."
            e is java.net.SocketTimeoutException || e is java.io.InterruptedIOException -> "The server took too long to answer."
            e is ClientException && e.statusCode == HTTP_NOT_FOUND -> "This doc is not there any more."
            else -> e.message ?: "Something went wrong."
        }

        const val HTTP_NOT_FOUND = 404
    }
}
