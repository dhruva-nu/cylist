package cylist.app.board

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.generated.apis.AuthApi
import cylist.api.generated.apis.BoardApi
import cylist.api.generated.apis.ProjectsApi
import cylist.api.generated.apis.TasksApi
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.MemberRead
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.ProjectRead
import cylist.api.generated.models.TaskCreate
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskType
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

/** Everything the Board screen draws, as of the last fetch. */
data class BoardState(
    val me: PersonRead? = null,
    val projects: List<ProjectRead> = emptyList(),
    val project: ProjectRead? = null,
    val columns: List<ColumnRead> = emptyList(),
    val tasks: List<TaskRead> = emptyList(),
    val members: List<MemberRead> = emptyList(),
    val columnId: UUID? = null,
    /** Whose cards to show; empty is everyone. Opens on you, as the web board does. */
    val who: Set<UUID> = emptySet(),
    val agentFilter: AgentFilter = AgentFilter.All,
    val loading: Boolean = true,
    val error: String? = null,
    /** Whether the board socket is open, so a change elsewhere shows here by itself. */
    val live: Boolean = false,
    val signedOut: Boolean = false,
)

/**
 * The Board screen's data: one project's columns, cards and people, refetched
 * whenever the board socket rings.
 *
 * The socket is held only while the screen is started ([connect] and
 * [disconnect] follow the lifecycle), so a phone in a pocket holds nothing
 * open. Its `ready` is an unconditional refetch, which is the whole resync:
 * whatever changed while the app was in the background is picked up when it
 * comes back.
 */
class BoardViewModel(private val session: Session) : ViewModel() {
    private val client = session.client
    private val _state = MutableStateFlow(BoardState())
    val state: StateFlow<BoardState> = _state.asStateFlow()

    private var socket: BoardSocket? = null
    private var started = false
    private var pending: Job? = null
    private var whoChosen = false

    init {
        viewModelScope.launch { firstLoad() }
    }

    private suspend fun firstLoad() {
        try {
            val (me, projects) = io {
                client.api(::AuthApi).meApiV1MeGet().person to
                    client.api(::ProjectsApi).listProjectsApiV1ProjectsGet()
            }
            _state.update { it.copy(me = me, projects = projects) }
            val first = projects.firstOrNull()
            if (first == null) {
                _state.update { it.copy(loading = false) }
                return
            }
            selectProject(first.key)
        } catch (e: Exception) {
            _state.update { it.copy(loading = false, error = describe(e)) }
        }
    }

    /** Switch the board to another project, and the socket with it. */
    fun selectProject(key: String) {
        val project = _state.value.projects.firstOrNull { it.key == key } ?: return
        whoChosen = false
        _state.update {
            it.copy(project = project, columns = emptyList(), tasks = emptyList(), members = emptyList(), columnId = null, loading = true)
        }
        socket?.stop()
        socket = client.boardSocket(project.key, Doorbell(project.key))
        if (started) socket?.start()
        refresh()
    }

    /** The screen is on: hold the socket open. Its `ready` refetches. */
    fun connect() {
        started = true
        socket?.start()
    }

    /** The screen went to the background: let the socket go. */
    fun disconnect() {
        started = false
        socket?.stop()
    }

    fun selectColumn(id: UUID) = _state.update { it.copy(columnId = id) }

    fun toggleWho(id: UUID) {
        whoChosen = true
        _state.update { it.copy(who = if (id in it.who) it.who - id else it.who + id) }
    }

    fun showEveryone() {
        whoChosen = true
        _state.update { it.copy(who = emptySet()) }
    }

    fun setAgentFilter(filter: AgentFilter) =
        _state.update { it.copy(agentFilter = if (it.agentFilter == filter) AgentFilter.All else filter) }

    /**
     * Add a card to the project. It lands in the board's first column, as on
     * the web, which also asks for a description: the server refuses an empty one.
     */
    fun addCard(title: String, description: String, onDone: (String?) -> Unit) {
        val project = _state.value.project ?: return
        viewModelScope.launch {
            val error = try {
                io {
                    client.api(::TasksApi).createTaskApiV1ProjectsProjectRefTasksPost(
                        project.key,
                        // The generated client writes every default, and the server
                        // takes a null for none of these, so each is given its value.
                        TaskCreate(
                            title = title.trim(),
                            description = description.trim(),
                            type = TaskType.feature,
                            priority = TaskPriority.p3,
                            subStatuses = emptyList(),
                            columnDueDates = emptyList(),
                            assigneeId = _state.value.me?.id,
                            prRefs = emptyList(),
                        ),
                    )
                }
                refresh()
                null
            } catch (e: Exception) {
                describe(e)
            }
            onDone(error)
        }
    }

    /** Refetch now, collapsing a burst of doorbells into one fetch. */
    fun refresh(debounceMs: Long = 0) {
        val key = _state.value.project?.key ?: return
        pending?.cancel()
        pending = viewModelScope.launch {
            if (debounceMs > 0) delay(debounceMs)
            load(key)
        }
    }

    private suspend fun load(key: String) {
        try {
            val board = io {
                Triple(
                    client.api(::BoardApi).listColumnsApiV1ProjectsProjectRefColumnsGet(key).columns.sortedBy { it.position },
                    client.api(::TasksApi).listTasksApiV1ProjectsProjectRefTasksGet(key),
                    client.api(::ProjectsApi).listMembersApiV1ProjectsProjectRefMembersGet(key).members,
                )
            }
            if (_state.value.project?.key != key) return
            val (columns, tasks, members) = board
            _state.update { current ->
                val me = current.me?.id
                current.copy(
                    columns = columns,
                    tasks = tasks,
                    members = members.filter { it.archivedAt == null },
                    columnId = current.columnId?.takeIf { id -> columns.any { it.id == id } } ?: defaultColumn(columns, tasks, me),
                    who = if (whoChosen) current.who else setOfNotNull(me?.takeIf { id -> members.any { it.id == id } }),
                    loading = false,
                    error = null,
                )
            }
        } catch (e: Exception) {
            _state.update { it.copy(loading = false, error = describe(e)) }
        }
    }

    /** Open on the first column that has something of yours in it, not an empty backlog. */
    private fun defaultColumn(columns: List<ColumnRead>, tasks: List<TaskRead>, me: UUID?): UUID? {
        val open = columns.dropLast(1)
        val mine = tasks.filter { onBoard(it) && (me == null || me in ownersOf(it)) }
        return open.firstOrNull { column -> mine.any { it.columnId == column.id } }?.id ?: columns.firstOrNull()?.id
    }

    override fun onCleared() {
        socket?.stop()
    }

    private inner class Doorbell(private val key: String) : BoardSocket.Listener {
        override fun onEvent(event: BoardEvent) {
            if (event == BoardEvent.Ready || event is BoardEvent.Changed) {
                viewModelScope.launch { if (_state.value.project?.key == key) refresh(DEBOUNCE_MS) }
            }
        }

        override fun onConnectedChanged(connected: Boolean) {
            if (_state.value.project?.key == key) _state.update { it.copy(live = connected) }
        }

        override fun onSignedOut() {
            _state.update { it.copy(live = false, signedOut = true) }
            // The socket was refused its token (4401): the whole app is signed out, not just the board.
            session.refused()
        }
    }

    private companion object {
        /** Collapse a burst of doorbells into one refetch, as the web app does. */
        const val DEBOUNCE_MS = 250L

        suspend fun <T> io(block: () -> T): T = withContext(Dispatchers.IO) { block() }

        fun describe(e: Exception): String = when (e) {
            is java.net.UnknownHostException, is java.net.ConnectException, is java.net.SocketTimeoutException ->
                "Can't reach the server."
            else -> e.message ?: "Something went wrong."
        }
    }
}
