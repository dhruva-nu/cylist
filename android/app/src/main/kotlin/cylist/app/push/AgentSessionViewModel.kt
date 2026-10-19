package cylist.app.push

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.generated.apis.AgentSessionsApi
import cylist.api.generated.apis.TasksApi
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import cylist.app.session.Session
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.util.UUID

/** One card's agent session, as the screen an agent notification opens draws it. */
data class AgentSessionUi(
    val reference: String,
    val title: String = "",
    /** The session the notification was about, or the card's most pressing one. */
    val session: AgentSessionRead? = null,
    /** Newest first, this session's lines only. */
    val feed: List<AgentFeedEntryRead> = emptyList(),
    val loading: Boolean = true,
    val error: String? = null,
    val answering: Boolean = false,
)

/**
 * Loads one card's agent session and keeps it current: the board socket rings
 * on every state change and every feed line, and each ring refetches, the way
 * the board does. Answering a question's option is the one write.
 */
class AgentSessionViewModel(private val session: Session, private val link: CardLink) : ViewModel() {
    private val client = session.client
    private val _state = MutableStateFlow(AgentSessionUi(reference = link.task))
    val state: StateFlow<AgentSessionUi> = _state.asStateFlow()

    private val socket: BoardSocket = client.boardSocket(link.task.substringBefore('-'), Doorbell())
    private var taskId: UUID? = null
    private var pending: Job? = null

    init {
        refresh()
    }

    fun connect() = socket.start()

    fun disconnect() = socket.stop()

    fun refresh(debounceMs: Long = 0) {
        pending?.cancel()
        pending = viewModelScope.launch {
            if (debounceMs > 0) delay(debounceMs)
            load()
        }
    }

    /** Answer the open question with one of its options, as the board would. */
    fun answer(promptId: UUID, key: String) {
        _state.update { it.copy(answering = true) }
        viewModelScope.launch {
            val error = try {
                io {
                    client.api(::AgentSessionsApi).answerAgentPromptApiV1TasksTaskRefAgentPromptsPromptIdAnswerPost(
                        promptId,
                        link.task,
                        AgentPromptAnswer(key = key),
                    )
                }
                null
            } catch (e: Exception) {
                describe(e)
            }
            _state.update { it.copy(answering = false, error = error) }
            load()
        }
    }

    private suspend fun load() {
        try {
            val (detail, feed) = io {
                client.api(::TasksApi).getTaskApiV1TasksTaskRefGet(link.task) to
                    client.api(::AgentSessionsApi).listAgentFeedApiV1TasksTaskRefAgentFeedGet(link.task, FEED_LINES)
            }
            taskId = detail.id
            val chosen = pick(detail.agentSessions.orEmpty(), link.sessionId)
            _state.update {
                it.copy(
                    title = detail.title,
                    session = chosen,
                    feed = feed.filter { line -> chosen == null || line.agentSessionId == chosen.id },
                    loading = false,
                    error = null,
                )
            }
        } catch (e: Exception) {
            _state.update { it.copy(loading = false, error = describe(e)) }
        }
    }

    override fun onCleared() = socket.stop()

    private inner class Doorbell : BoardSocket.Listener {
        override fun onEvent(event: BoardEvent) {
            val mine = when (event) {
                BoardEvent.Ready, is BoardEvent.Changed -> true
                is BoardEvent.AgentFeed -> event.taskId == taskId?.toString()
                is BoardEvent.Error -> false
            }
            if (mine) viewModelScope.launch { refresh(DEBOUNCE_MS) }
        }

        override fun onConnectedChanged(connected: Boolean) = Unit

        override fun onSignedOut() = session.refused()
    }

    companion object {
        private const val FEED_LINES = 50
        private const val DEBOUNCE_MS = 250L

        /**
         * The notification's own session when the card still has it, else the
         * one a person would look at first: waiting, then working, then the
         * most recent of the rest.
         */
        fun pick(sessions: List<AgentSessionRead>, sessionId: String?): AgentSessionRead? =
            sessions.firstOrNull { it.id.toString() == sessionId }
                ?: sessions.firstOrNull { it.state == AgentSessionState.waiting }
                ?: sessions.firstOrNull { it.state == AgentSessionState.working }
                ?: sessions.maxByOrNull { it.stateChangedAt }

        private suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }

        private fun describe(e: Exception): String = when (e) {
            is java.net.UnknownHostException, is java.net.ConnectException, is java.net.SocketTimeoutException ->
                "Can't reach the server."
            else -> e.message ?: "Something went wrong."
        }
    }
}
