package cylist.app.today

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.everyPage
import cylist.api.serverMessage
import cylist.api.generated.apis.AuthApi
import cylist.api.generated.apis.BoardApi
import cylist.api.generated.apis.FilesApi
import cylist.api.generated.apis.PeopleApi
import cylist.api.generated.apis.TasksApi
import cylist.api.generated.apis.TodayApi
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.FiledItem
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.PickCreate
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TodayPlan
import cylist.api.generated.models.TodoCreate
import cylist.api.generated.models.TodoState
import cylist.api.generated.models.TodoUpdate
import cylist.api.generated.models.WrapUp
import cylist.api.generated.models.WrapUpResult
import cylist.app.session.Session
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.time.Clock
import java.time.LocalDate
import java.util.UUID

/** Everything the Today screen draws, as of the last fetch. */
data class TodayState(
    val projectKey: String? = null,
    /** Today in the phone's zone, as of the last fetch: the web's `localToday`. */
    val today: LocalDate,
    /** The day on screen. Opening Today always starts on today, as on the web. */
    val day: LocalDate,
    val me: PersonRead? = null,
    /** The plan for [day]; null until it has been read. */
    val plan: TodayPlan? = null,
    /** To-dos still open today, whichever day is on screen: the Today tab's count. */
    val todayOpen: Int = 0,
    val tasks: List<TaskSummary> = emptyList(),
    val columns: List<ColumnRead> = emptyList(),
    /** The people directory, for `@` tags; archived people are left out, as on the web. */
    val people: List<PersonRead> = emptyList(),
    /** The project's files and links, for `>` tags. */
    val files: List<FiledItem> = emptyList(),
    val loading: Boolean = true,
    val error: String? = null,
    /** A tick the server refused (a role kept out of a column, say), by to-do: said on its row. */
    val todoErrors: Map<UUID, String> = emptyMap(),
    /** Whether the board socket is open, so a change on the board shows here by itself. */
    val live: Boolean = false,
) {
    /** One of your cards that is late and not on today's plan, if any. */
    val lateUnplanned: TaskSummary?
        get() = if (day != today || plan == null) {
            null
        } else {
            lateAndUnplanned(tasks, columns, me?.id, today, plan.picks.map { it.task.id }.toSet())
        }
}

/**
 * The Today screen's data: the signed-in person's plan for one day on one
 * project, through the same `/today` routes the web planner uses, so a day
 * planned here is the same day in the browser.
 *
 * The plan follows its cards (the server syncs it on every read), so while the
 * screen is started it holds the project's board socket, as the Board screen
 * does, and reads the plan again whenever a card changes anywhere.
 */
class TodayViewModel(
    private val session: Session,
    private val clock: Clock = Clock.systemDefaultZone(),
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val client = session.client
    private val _state = MutableStateFlow(TodayState(today = LocalDate.now(clock), day = LocalDate.now(clock)))
    val state: StateFlow<TodayState> = _state.asStateFlow()

    private var socket: BoardSocket? = null
    private var started = false
    private var pending: Job? = null

    /** The zone the server cuts "today" by: the phone's, as the web sends the browser's. */
    private val zone: String get() = clock.zone.id

    init {
        viewModelScope.launch {
            try {
                val (me, people) = io {
                    client.api(::AuthApi).meApiV1MeGet().person to
                        everyPage { after ->
                            client.api(::PeopleApi).listPeopleApiV1PeopleGet(after = after).let { it.items to it.next }
                        }
                }
                _state.update { it.copy(me = me, people = people) }
            } catch (e: Exception) {
                _state.update { it.copy(error = describe(e)) }
            }
        }
    }

    /** Plan on this project: the one the Board tab is on. Changing it starts again on today. */
    fun setProject(key: String) {
        if (_state.value.projectKey == key) return
        val today = LocalDate.now(clock)
        _state.update {
            it.copy(
                projectKey = key, today = today, day = today, plan = null, todayOpen = 0,
                tasks = emptyList(), columns = emptyList(), files = emptyList(), loading = true, error = null,
                todoErrors = emptyMap(),
            )
        }
        socket?.stop()
        socket = client.boardSocket(key, Doorbell(key))
        if (started) socket?.start()
        viewModelScope.launch {
            try {
                val files = io { everyPage { after ->
                        client.api(::FilesApi).listProjectItemsApiV1ProjectsProjectRefItemsGet(key, after = after).let { it.items to it.next }
                    } }
                if (_state.value.projectKey == key) _state.update { it.copy(files = files) }
            } catch (_: Exception) {
                // A tag whose file has not arrived reads as the plain text it is.
            }
        }
        refresh()
    }

    /** The screen is on: hold the socket open; its `ready` reads the plan again. */
    fun connect() {
        started = true
        socket?.start()
    }

    fun disconnect() {
        started = false
        socket?.stop()
    }

    /** Show another day's plan. */
    fun showDay(day: LocalDate) {
        if (day == _state.value.day) return
        _state.update { it.copy(day = day, plan = null, loading = true, todoErrors = emptyMap()) }
        refresh()
    }

    /** Back to today, which is also what the screen opens on. */
    fun backToToday() = showDay(LocalDate.now(clock))

    /** Put a card on the day on screen. Picking one already there is harmless: the server answers with the same pick. */
    fun pick(task: TaskSummary, onDone: (String?) -> Unit = {}) = mutate(onDone) { key ->
        client.api(::TodayApi).addPickApiV1ProjectsProjectRefTodayPicksPost(key, PickCreate(task.reference, _state.value.day.toString()))
    }

    /** Take a card off the day, with its to-dos. */
    fun unpick(pickId: UUID, onDone: (String?) -> Unit = {}) = mutate(onDone) {
        client.api(::TodayApi).removePickApiV1TodayPicksPickIdDelete(pickId)
    }

    fun addTodo(pickId: UUID, text: String, onDone: (String?) -> Unit) {
        val words = text.trim()
        if (words.isEmpty()) return
        mutate(onDone) { client.api(::TodayApi).addTodoApiV1TodayPicksPickIdTodosPost(pickId, TodoCreate(words)) }
    }

    fun deleteTodo(todoId: UUID) = mutate {
        client.api(::TodayApi).deleteTodoApiV1TodayTodosTodoIdDelete(todoId)
    }

    /**
     * Tick or untick a to-do. The box answers at once, as on the web; a tick
     * the server refuses springs back on the refetch and says why on its row.
     */
    fun tick(todoId: UUID, done: Boolean) {
        val state = if (done) TodoState.done else TodoState.`open`
        _state.update { current ->
            current.copy(
                plan = current.plan?.let { plan ->
                    plan.copy(picks = plan.picks.map { pick -> pick.copy(todos = pick.todos.map { if (it.id == todoId) it.copy(state = state) else it }) })
                },
                todoErrors = current.todoErrors - todoId,
            )
        }
        viewModelScope.launch {
            try {
                io {
                    client.api(::TodayApi).updateTodoApiV1TodayTodosTodoIdPatch(
                        todoId,
                        TodoUpdate(state = if (done) TodoUpdate.State.done else TodoUpdate.State.`open`),
                    )
                }
            } catch (e: Exception) {
                _state.update { it.copy(todoErrors = it.todoErrors + (todoId to describe(e))) }
            }
            refresh()
        }
    }

    /**
     * Close [day]: [move] goes to [toDay] and every other open to-do stays as
     * not done, by the web's own `POST /today/wrap-up`.
     */
    fun wrapUp(day: LocalDate, toDay: LocalDate, move: List<UUID>, onDone: (Result<WrapUpResult>) -> Unit) {
        val key = _state.value.projectKey ?: return
        viewModelScope.launch {
            val result = try {
                Result.success(
                    io {
                        client.api(::TodayApi).wrapUpApiV1ProjectsProjectRefTodayWrapUpPost(
                            key,
                            WrapUp(day = day.toString(), toDay = toDay.toString(), move = move, timezone = zone),
                        )
                    },
                )
            } catch (e: Exception) {
                Result.failure(Exception(describe(e), e))
            }
            refresh()
            onDone(result)
        }
    }

    /** Read the plan (and the board it is picked from) again, collapsing a burst into one read. */
    fun refresh(debounceMs: Long = 0) {
        val key = _state.value.projectKey ?: return
        pending?.cancel()
        pending = viewModelScope.launch {
            if (debounceMs > 0) delay(debounceMs)
            load(key)
        }
    }

    private suspend fun load(key: String) {
        val today = LocalDate.now(clock)
        val day = _state.value.day
        try {
            val loaded = io {
                coroutineScope {
                    val plan = async { client.api(::TodayApi).getPlanApiV1ProjectsProjectRefTodayGet(key, day.toString(), zone) }
                    val todayPlan = if (day == today) null else async { client.api(::TodayApi).getPlanApiV1ProjectsProjectRefTodayGet(key, today.toString(), zone) }
                    // The top of each column, as deep as one request goes. What is due
                    // today sits near the top of the columns it is in; a card buried
                    // past the hundredth in its column is not what a day is planned from.
                    val tasks = async {
                        client.api(::TasksApi).listTasksApiV1ProjectsProjectRefTasksGet(key, limit = BOARD_DEPTH).columns.flatMap { it.items }
                    }
                    val columns = async { client.api(::BoardApi).listColumnsApiV1ProjectsProjectRefColumnsGet(key).columns.sortedBy { it.position } }
                    Loaded(plan.await(), todayPlan?.await()?.openCount, tasks.await(), columns.await())
                }
            }
            if (_state.value.projectKey != key || _state.value.day != day) return
            _state.update {
                it.copy(
                    today = today,
                    plan = loaded.plan,
                    todayOpen = loaded.todayOpen ?: loaded.plan.openCount,
                    tasks = loaded.tasks,
                    columns = loaded.columns,
                    loading = false,
                    error = null,
                )
            }
        } catch (e: Exception) {
            _state.update { it.copy(loading = false, error = describe(e)) }
        }
    }

    private class Loaded(val plan: TodayPlan, val todayOpen: Int?, val tasks: List<TaskSummary>, val columns: List<ColumnRead>)

    /** A change to the plan, then a fresh read of it; [onDone] gets the error to show, or null. */
    private fun mutate(onDone: (String?) -> Unit = {}, call: (key: String) -> Any) {
        val key = _state.value.projectKey ?: return
        viewModelScope.launch {
            val error = try {
                io { call(key) }
                null
            } catch (e: Exception) {
                describe(e)
            }
            if (error != null) _state.update { it.copy(error = error) }
            refresh()
            onDone(error)
        }
    }

    private suspend fun <T> io(block: suspend () -> T): T = withContext(io) { block() }

    override fun onCleared() {
        socket?.stop()
    }

    private inner class Doorbell(private val key: String) : BoardSocket.Listener {
        override fun onEvent(event: BoardEvent) {
            if (event == BoardEvent.Ready || event is BoardEvent.Changed) {
                viewModelScope.launch { if (_state.value.projectKey == key) refresh(DEBOUNCE_MS) }
            }
        }

        override fun onConnectedChanged(connected: Boolean) {
            if (_state.value.projectKey == key) _state.update { it.copy(live = connected) }
        }

        override fun onSignedOut() {
            _state.update { it.copy(live = false) }
            session.refused()
        }
    }

    private companion object {
        /** How far down each column the plan looks for cards: the most one request returns. */
        const val BOARD_DEPTH = 100

        /** Collapse a burst of doorbells into one read, as the Board does. */
        const val DEBOUNCE_MS = 250L

        fun describe(e: Exception): String = when (e) {
            is java.net.UnknownHostException, is java.net.ConnectException, is java.net.SocketTimeoutException ->
                "Can't reach the server."
            else -> serverMessage(e) ?: e.message ?: "Something went wrong."
        }
    }
}
