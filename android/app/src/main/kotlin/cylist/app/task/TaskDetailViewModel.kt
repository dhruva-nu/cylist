package cylist.app.task

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.ApiFailure
import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.ChecklistItemRead
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.MemberRead
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TemplateRead
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.IOException
import kotlin.coroutines.cancellation.CancellationException
import java.util.UUID

/** A move held until somebody says yes: taking a finished card back onto the board. */
data class PendingMove(val columnId: UUID, val outcome: String?, val from: String, val to: String)

/** Everything the Task detail screen draws, as of the last fetch. */
data class TaskDetailState(
    val ref: String,
    val task: TaskDetail? = null,
    val columns: List<ColumnRead> = emptyList(),
    val templates: List<TemplateRead> = emptyList(),
    val members: List<MemberRead> = emptyList(),
    val me: PersonRead? = null,
    val reporter: String? = null,
    /** What the agents on the card read, edited, ran and said, newest first. */
    val feed: List<AgentFeedEntryRead> = emptyList(),
    val loading: Boolean = true,
    /** Why the card could not be loaded at all. */
    val error: String? = null,
    /** Why the last thing asked of the card was refused, in the server's words. */
    val notice: String? = null,
    val draft: String = "",
    val sending: Boolean = false,
    val moving: Boolean = false,
    /** Checklist items, sub-tasks and prompts with a change on its way, by id. */
    val busy: Set<UUID> = emptySet(),
    val pendingMove: PendingMove? = null,
    /** Whether the board socket is open, so a change made elsewhere shows here by itself. */
    val live: Boolean = false,
)

/**
 * One card: read it, move it, tick its parts off, talk on it, and answer the
 * agent on it.
 *
 * After every change the card is fetched again rather than patched by hand:
 * the server works out what a move or a tick did to the rest of the card
 * (finished time, outcome, stage, counts), and saying it twice is how the
 * phone and the web would start to disagree. The project's board socket is
 * held while the screen is started, as the Board screen does, so an agent's
 * feed and anything changed on the web show up without a pull.
 */
class TaskDetailViewModel(
    private val source: TaskSource,
    private val projectKey: String,
    ref: String,
    private val io: CoroutineDispatcher = Dispatchers.IO,
    private val debounceMs: Long = DEBOUNCE_MS,
) : ViewModel() {
    private val _state = MutableStateFlow(TaskDetailState(ref = ref))
    val state: StateFlow<TaskDetailState> = _state.asStateFlow()

    private val ref get() = _state.value.ref
    private var watch: TaskSource.Watch? = null
    private var pendingTask: Job? = null
    private var pendingFeed: Job? = null

    init {
        viewModelScope.launch { firstLoad() }
    }

    private suspend fun firstLoad() {
        try {
            val board = io {
                Board(source.me(), source.columns(projectKey), source.templates(projectKey), source.members(projectKey))
            }
            _state.update {
                it.copy(me = board.me, columns = board.columns, templates = board.templates, members = board.members.filter { m -> m.archivedAt == null })
            }
        } catch (e: Exception) {
            e.rethrowIfCancelled()
            _state.update { it.copy(loading = false, error = describe(e)) }
            return
        }
        loadTask()
        // Who added the card does not change; it is asked once, and a failure only leaves the row out.
        val reporter = try {
            io { source.reporter(ref) }
        } catch (e: Exception) {
            e.rethrowIfCancelled()
            null
        }
        _state.update { it.copy(reporter = reporter) }
    }

    private data class Board(val me: PersonRead?, val columns: List<ColumnRead>, val templates: List<TemplateRead>, val members: List<MemberRead>)

    private suspend fun loadTask() {
        try {
            val task = io { source.task(ref) }
            val feed = if (task.agentSessions.orEmpty().isNotEmpty()) io { source.feed(ref) } else emptyList()
            _state.update { it.copy(task = task, feed = feed, loading = false, error = null) }
        } catch (e: Exception) {
            e.rethrowIfCancelled()
            _state.update { it.copy(loading = false, error = if (it.task == null) describe(e) else null, notice = if (it.task != null) describe(e) else it.notice) }
        }
    }

    private suspend fun loadFeed() {
        try {
            val feed = io { source.feed(ref) }
            _state.update { it.copy(feed = feed) }
        } catch (e: Exception) {
            e.rethrowIfCancelled()
            _state.update { it.copy(notice = describe(e)) }
        }
    }

    /** Fetch the card again now, or after [delayMs] collapsing a burst of doorbells into one. */
    fun refresh(delayMs: Long = 0) {
        pendingTask?.cancel()
        pendingTask = viewModelScope.launch {
            if (delayMs > 0) delay(delayMs)
            loadTask()
        }
    }

    private fun refreshFeed(delayMs: Long) {
        pendingFeed?.cancel()
        pendingFeed = viewModelScope.launch {
            if (delayMs > 0) delay(delayMs)
            loadFeed()
        }
    }

    /** The screen is on: hold the project's board socket. Its `ready` refetches. */
    fun connect() {
        val watching = watch ?: source.watch(projectKey, Doorbell()).also { watch = it }
        watching.start()
    }

    /** The screen went to the background: let the socket go. */
    fun disconnect() {
        watch?.stop()
        _state.update { it.copy(live = false) }
    }

    fun dismissNotice() = _state.update { it.copy(notice = null) }

    // --- Moving ----------------------------------------------------------------

    /**
     * Move the card to [columnId], ending on [outcome] where that is the
     * board's last column and it has outcomes. A move the phone already knows
     * the server will refuse is refused here with the server's reasons; one
     * that takes a finished card back onto the board waits for [confirmMove].
     */
    fun move(columnId: UUID, outcome: String? = null) {
        val current = _state.value
        val task = current.task ?: return
        if (current.moving) return
        when (val plan = planMove(task, current.columns, current.templates, columnId, outcome)) {
            MovePlan.Stay -> Unit
            is MovePlan.Refused -> _state.update { it.copy(notice = plan.reason) }
            is MovePlan.Reopen -> _state.update { it.copy(pendingMove = PendingMove(columnId, outcome, plan.from, plan.to)) }
            MovePlan.Go -> send(columnId, outcome)
        }
    }

    /** Yes: take the finished card back onto the board. */
    fun confirmMove() {
        val pending = _state.value.pendingMove ?: return
        _state.update { it.copy(pendingMove = null) }
        send(pending.columnId, pending.outcome)
    }

    fun cancelMove() = _state.update { it.copy(pendingMove = null) }

    private fun send(columnId: UUID, outcome: String?) {
        // At the foot of the column, where the web board drops a card; the server clamps it.
        val position = _state.value.columns.firstOrNull { it.id == columnId }?.taskCount ?: 0
        _state.update { it.copy(moving = true, notice = null) }
        viewModelScope.launch {
            try {
                val moved = io { source.move(ref, columnId, position, outcome) }
                _state.update { it.copy(task = moved, moving = false) }
            } catch (e: Exception) {
                e.rethrowIfCancelled()
                _state.update { it.copy(moving = false, notice = describe(e)) }
            }
            refresh()
        }
    }

    // --- Ticking ---------------------------------------------------------------

    /** Tick a checklist item, or untick it. A cancelled one is settled already and stays so. */
    fun toggle(item: ChecklistItemRead) {
        val next = nextChecklistState(item.state) ?: return
        change(item.id) { source.setChecklistState(item.id, next) }
    }

    /** Finish a sub-task, or open it again. A cancelled one is settled already. */
    fun toggle(subtask: TaskSummary) {
        if (subtask.status == TaskStatus.cancelled) return
        change(subtask.id) { source.finishSubtask(subtask.reference, finished = subtask.finishedAt == null) }
    }

    private fun change(id: UUID, call: () -> Unit) {
        if (id in _state.value.busy) return
        _state.update { it.copy(busy = it.busy + id, notice = null) }
        viewModelScope.launch {
            try {
                io { call() }
            } catch (e: Exception) {
                e.rethrowIfCancelled()
                _state.update { it.copy(notice = describe(e)) }
            }
            loadTask()
            _state.update { it.copy(busy = it.busy - id) }
        }
    }

    // --- Talking ---------------------------------------------------------------

    fun setDraft(text: String) = _state.update { it.copy(draft = text) }

    /**
     * Post the composer's text as a comment, in the signed-in person's name
     * (an API token's comment with no author reads as an agent's). The box
     * empties only once the server has it.
     */
    fun sendComment() {
        val body = _state.value.draft.trim()
        if (body.isEmpty() || _state.value.sending) return
        _state.update { it.copy(sending = true, notice = null) }
        viewModelScope.launch {
            try {
                val author = _state.value.me?.id
                io { source.comment(ref, body, author) }
                _state.update { it.copy(sending = false, draft = "") }
                loadTask()
            } catch (e: Exception) {
                e.rethrowIfCancelled()
                _state.update { it.copy(sending = false, notice = describe(e)) }
            }
        }
    }

    /**
     * Answer an agent's question or permission. The first answer wins on the
     * server; a later one is a conflict saying who answered what, which is
     * shown as it came, and the card is fetched again to show it answered.
     */
    fun answer(prompt: AgentPromptRead, answer: AgentPromptAnswer) {
        if (prompt.id in _state.value.busy) return
        _state.update { it.copy(busy = it.busy + prompt.id, notice = null) }
        viewModelScope.launch {
            try {
                io { source.answer(ref, prompt.id, answer) }
            } catch (e: Exception) {
                e.rethrowIfCancelled()
                val failure = ApiFailure.of(e)
                _state.update {
                    it.copy(notice = if (failure?.isConflict == true) failure.message ?: describe(e) else "The answer did not reach the agent: ${describe(e)}")
                }
            }
            loadTask()
            _state.update { it.copy(busy = it.busy - prompt.id) }
        }
    }

    override fun onCleared() {
        watch?.stop()
    }

    private inner class Doorbell : BoardSocket.Listener {
        override fun onEvent(event: BoardEvent) {
            val id = _state.value.task?.id
            viewModelScope.launch {
                when {
                    event is BoardEvent.AgentFeed && id != null && ringsFor(event, id) -> refreshFeed(debounceMs)
                    event == BoardEvent.Ready || event is BoardEvent.Changed -> refresh(debounceMs)
                }
            }
        }

        override fun onConnectedChanged(connected: Boolean) = _state.update { it.copy(live = connected) }

        // [ServerTaskSource.watch] has already told the session, which signs the app out.
        override fun onSignedOut() = _state.update { it.copy(live = false) }
    }

    private suspend fun <T> io(block: () -> T): T = withContext(io) { block() }

    companion object {
        /** Collapse a burst of doorbells into one refetch, as the board does. */
        const val DEBOUNCE_MS = 250L

        /** A failure as a sentence for the screen: the server's own words where it gave some. */
        fun describe(e: Throwable): String {
            val failure = ApiFailure.of(e)
            failure?.message?.let { return it }
            return when {
                failure != null -> "The server refused that (${failure.status})."
                // UnknownHost, Connect and SocketTimeout among them.
                e is IOException -> "Can't reach the server."
                else -> e.message ?: "Something went wrong."
            }
        }
    }
}

/**
 * A fetch cancelled because a newer one replaced it ([TaskDetailViewModel.refresh]
 * cancels the one pending) is not a failure to show: let the cancellation through.
 */
private fun Exception.rethrowIfCancelled() {
    if (this is CancellationException) throw this
}
