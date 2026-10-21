package cylist.app.task

import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptOption
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionReason
import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import cylist.api.generated.models.ChecklistItemRead
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.PersonKind
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.StageRead
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import cylist.api.generated.models.TemplateRead
import java.util.UUID

/* Cards, columns, sessions and prompts for the Task detail tests, built with only what each test cares about. */

val projectId: UUID = UUID.randomUUID()

fun person(name: String, id: UUID = UUID.randomUUID()) = PersonRead(
    id = id, name = name, kind = PersonKind.team, title = "", responsibilities = "",
    email = null, colour = "#4A7B8C", archivedAt = null, createdAt = "2026-10-01T00:00:00Z",
    isAgent = false, hasAccount = true, inviteIsPending = false,
)

fun column(name: String, position: Int, outcomes: List<String> = emptyList()) = ColumnRead(
    id = UUID.randomUUID(), projectId = projectId, name = name, description = "", position = position,
    outcomes = outcomes, taskCount = 3,
)

/** Backlog, In progress, Review, and Done divided into Done / Cancelled / In prod. */
val board: List<ColumnRead> = listOf(
    column("Backlog", 0),
    column("In progress", 1),
    column("Review", 2),
    column("Done", 3, listOf("Done", "Cancelled", "In prod")),
)

fun subtask(ref: String, title: String = ref, finishedAt: String? = null, status: TaskStatus = TaskStatus.active) = TaskSummary(
    id = UUID.randomUUID(), projectId = projectId, reference = ref, number = 1,
    parentId = UUID.randomUUID(), parentReference = "ATL-1", subNumber = 1, columnId = null, position = null,
    title = title, type = TaskType.feature, priority = TaskPriority.p3,
    subStatuses = emptyList(), subStatusIndex = null, dueDate = null, columnDueDates = emptyList(), nextDueDate = null,
    assignee = person("Aditi K"), status = status, templateId = null, templateName = null,
    goalId = null, goalReference = null, goalName = null, goalColour = null, jiraRef = null, prRefs = emptyList(),
    waitingOn = emptyList(), commentCount = 0, checklist = emptyList(), outcome = null, outcomeIndex = null,
    finishedAt = finishedAt, openSubtaskCount = 0, subtaskCount = 0, subtaskAssignees = emptyList(),
    createdAt = "2026-10-01T00:00:00Z",
)

fun item(title: String, state: ChecklistState = ChecklistState.`open`, position: Int = 0) = ChecklistItemRead(
    id = UUID.randomUUID(), taskId = UUID.randomUUID(), title = title, state = state, position = position,
    createdAt = "2026-10-01T00:00:00Z",
)

fun detail(
    columnId: UUID? = board[1].id,
    subtasks: List<TaskSummary> = emptyList(),
    checklist: List<ChecklistItemRead> = emptyList(),
    templateId: UUID? = null,
    outcome: String? = null,
    finishedAt: String? = null,
    sessions: List<AgentSessionRead> = emptyList(),
    subStatuses: List<String> = emptyList(),
    subStatusIndex: Int? = null,
    archivedAt: String? = null,
    parentId: UUID? = null,
) = TaskDetail(
    id = UUID.randomUUID(), projectId = projectId, reference = "ATL-1", number = 1,
    parentId = parentId, parentReference = null, subNumber = null, columnId = columnId, position = 0,
    title = "Device list returns duplicates", description = "", type = TaskType.bug, priority = TaskPriority.p0,
    subStatuses = subStatuses, subStatusIndex = subStatusIndex, dueDate = null, columnDueDates = emptyList(), nextDueDate = null,
    assignee = person("Dhruva N"), status = TaskStatus.active, templateId = templateId, templateName = null,
    goalId = null, goalReference = null, goalName = null, goalColour = null, jiraRef = null, prRefs = emptyList(),
    waitingOn = emptyList(), commentCount = 0, checklist = checklist, outcome = outcome, outcomeIndex = null,
    finishedAt = finishedAt, openSubtaskCount = subtasks.count { it.finishedAt == null }, subtaskCount = subtasks.size,
    subtaskAssignees = emptyList(), createdAt = "2026-10-01T00:00:00Z", comments = emptyList(), commentsNext = null, subtasks = subtasks,
    archivedAt = archivedAt, agentSessions = sessions,
)

fun template(name: String, allowed: List<ColumnRead>, outcomesIn: Map<ColumnRead, List<String>> = emptyMap()) = TemplateRead(
    id = UUID.randomUUID(), projectId = projectId, name = name, description = "",
    stages = allowed.map { StageRead(columnId = it.id, columnName = it.name, subStageLabels = emptyList(), allowedOutcomes = outcomesIn[it].orEmpty()) },
    allowedColumnIds = allowed.map { it.id }, taskCount = 0, createdAt = "2026-10-01T00:00:00Z",
)

fun prompt(
    kind: AgentPromptKind = AgentPromptKind.question,
    status: AgentPromptStatus = AgentPromptStatus.`open`,
    options: List<String> = listOf("25", "40"),
    allowsReply: Boolean = kind == AgentPromptKind.question,
    askedAt: String = "2026-10-19T09:00:00Z",
    expiresAt: String = "2026-10-19T09:30:00Z",
    answeredBy: String? = null,
    answeredById: UUID? = null,
    answer: String? = null,
    closedReason: String? = null,
    ownerId: UUID? = null,
    owner: String? = null,
) = AgentPromptRead(
    id = UUID.randomUUID(), agentSessionId = UUID.randomUUID(), taskId = UUID.randomUUID(), kind = kind,
    text = if (kind == AgentPromptKind.question) "25 devices or 40?" else "rm -rf build/",
    tool = if (kind == AgentPromptKind.permission) "Bash" else null,
    options = if (kind == AgentPromptKind.permission) {
        listOf(AgentPromptOption("allow_once", "Allow once"), AgentPromptOption("allow_session", "Allow for this session"), AgentPromptOption("deny", "Deny"))
    } else {
        options.map { AgentPromptOption(it.lowercase(), it) }
    },
    allowsReply = allowsReply, status = status, answer = answer, answerKey = null, answeredBy = answeredBy,
    answeredById = answeredById, ownerId = ownerId, owner = owner, answeredAt = null, closedReason = closedReason, askedAt = askedAt, expiresAt = expiresAt, closedAt = null,
)

fun session(
    state: AgentSessionState = AgentSessionState.working,
    reason: AgentSessionReason? = null,
    note: String? = null,
    endedAt: String? = null,
    prompt: AgentPromptRead? = null,
) = AgentSessionRead(
    id = UUID.randomUUID(), taskId = UUID.randomUUID(), actorLabel = "Dhruva N", clientName = "Claude Code",
    state = state, reason = reason, note = note, startedAt = "2026-10-19T08:00:00Z", stateChangedAt = "2026-10-19T08:30:00Z",
    lastSeenAt = "2026-10-19T08:58:00Z", endedAt = endedAt, dismissedAt = null, prompt = prompt,
)
