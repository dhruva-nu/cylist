package cylist.app.task

import cylist.api.BoardEvent
import cylist.api.BoardSocket
import cylist.api.generated.apis.AgentSessionsApi
import cylist.api.generated.apis.AuthApi
import cylist.api.generated.apis.BoardApi
import cylist.api.generated.apis.ProjectsApi
import cylist.api.generated.apis.TasksApi
import cylist.api.generated.apis.TemplatesApi
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.ChecklistItemUpdate
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.CommentCreate
import cylist.api.generated.models.MemberRead
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TaskFinish
import cylist.api.generated.models.TaskMove
import cylist.api.generated.models.TemplateRead
import cylist.app.session.Session
import java.util.UUID

/**
 * Everything the Task detail screen asks of the server, as blocking calls.
 *
 * An interface so [TaskDetailViewModel] can be tested without one;
 * [ServerTaskSource] is the real thing, over the session's client.
 */
interface TaskSource {
    fun me(): PersonRead?
    fun task(ref: String): TaskDetail
    fun columns(projectKey: String): List<ColumnRead>
    fun templates(projectKey: String): List<TemplateRead>
    fun members(projectKey: String): List<MemberRead>

    /** Who added the card, from its oldest history entry; null if the history does not say. */
    fun reporter(ref: String): String?
    fun feed(ref: String): List<AgentFeedEntryRead>
    fun move(ref: String, columnId: UUID, position: Int, outcome: String?): TaskDetail
    fun setChecklistState(itemId: UUID, state: ChecklistState)
    fun finishSubtask(ref: String, finished: Boolean)
    /** Post a comment in [authorId]'s name: the web app names its author too, and with none the server shows it as an agent's. */
    fun comment(ref: String, body: String, authorId: UUID?)
    fun answer(ref: String, promptId: UUID, answer: AgentPromptAnswer): AgentPromptRead

    /** The project's board socket, unopened: its doorbells say when to refetch. */
    fun watch(projectKey: String, listener: BoardSocket.Listener): Watch

    interface Watch {
        fun start()
        fun stop()
    }
}

/** [TaskSource] over the generated client, signed in as [session]. */
class ServerTaskSource(private val session: Session) : TaskSource {
    private val client get() = session.client

    override fun me(): PersonRead? = client.api(::AuthApi).meApiV1MeGet().person

    override fun task(ref: String): TaskDetail = client.api(::TasksApi).getTaskApiV1TasksTaskRefGet(ref)

    override fun columns(projectKey: String): List<ColumnRead> =
        client.api(::BoardApi).listColumnsApiV1ProjectsProjectRefColumnsGet(projectKey).columns.sortedBy { it.position }

    override fun templates(projectKey: String): List<TemplateRead> =
        client.api(::TemplatesApi).listTemplatesApiV1ProjectsProjectRefTemplatesGet(projectKey)

    override fun members(projectKey: String): List<MemberRead> =
        client.api(::ProjectsApi).listMembersApiV1ProjectsProjectRefMembersGet(projectKey).members

    override fun reporter(ref: String): String? {
        val tasks = client.api(::TasksApi)
        // Newest first, a page at a time: one entry per page makes the page
        // count the entry count, and the last page the oldest entry.
        val first = tasks.taskHistoryApiV1TasksTaskRefHistoryGet(ref, page = 1, perPage = 1)
        val oldest = if (first.pages <= 1) first else tasks.taskHistoryApiV1TasksTaskRefHistoryGet(ref, page = first.pages, perPage = 1)
        return reporterOf(oldest.propertyEntries.lastOrNull())
    }

    override fun feed(ref: String): List<AgentFeedEntryRead> =
        client.api(::AgentSessionsApi).listAgentFeedApiV1TasksTaskRefAgentFeedGet(ref, limit = FEED_LIMIT)

    override fun move(ref: String, columnId: UUID, position: Int, outcome: String?): TaskDetail =
        client.api(::TasksApi).moveTaskApiV1TasksTaskRefMovePost(ref, TaskMove(columnId = columnId, position = position, outcome = outcome))

    override fun setChecklistState(itemId: UUID, state: ChecklistState) {
        client.api(::TasksApi).updateChecklistItemApiV1ChecklistItemIdPatch(itemId, ChecklistItemUpdate(state = state))
    }

    override fun finishSubtask(ref: String, finished: Boolean) {
        client.api(::TasksApi).finishTaskApiV1TasksTaskRefFinishPost(ref, TaskFinish(finished = finished))
    }

    override fun comment(ref: String, body: String, authorId: UUID?) {
        client.api(::TasksApi).addCommentApiV1TasksTaskRefCommentsPost(ref, CommentCreate(body = body, authorId = authorId))
    }

    override fun answer(ref: String, promptId: UUID, answer: AgentPromptAnswer): AgentPromptRead =
        client.api(::AgentSessionsApi).answerAgentPromptApiV1TasksTaskRefAgentPromptsPromptIdAnswerPost(promptId, ref, answer)

    override fun watch(projectKey: String, listener: BoardSocket.Listener): TaskSource.Watch {
        val socket = client.boardSocket(
            projectKey,
            object : BoardSocket.Listener by listener {
                override fun onSignedOut() {
                    listener.onSignedOut()
                    // Refused its token (4401): the whole app is signed out, as the board does.
                    session.refused()
                }
            },
        )
        return object : TaskSource.Watch {
            override fun start() = socket.start()

            override fun stop() = socket.stop()
        }
    }

    private companion object {
        /** As many feed lines as the web app's panel asks for. */
        const val FEED_LIMIT = 100
    }
}

/** Whether a board doorbell is about this card: anything committed, or a feed line on it. */
fun ringsFor(event: BoardEvent, taskId: UUID): Boolean = when (event) {
    BoardEvent.Ready, is BoardEvent.Changed -> true
    is BoardEvent.AgentFeed -> event.taskId == taskId.toString()
    is BoardEvent.Error -> false
}
