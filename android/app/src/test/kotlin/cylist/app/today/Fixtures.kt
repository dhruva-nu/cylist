package cylist.app.today

import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.FiledItem
import cylist.api.generated.models.ItemKind
import cylist.api.generated.models.ItemSource
import cylist.api.generated.models.PersonKind
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.PickRead
import cylist.api.generated.models.PickedCard
import cylist.api.generated.models.Sensitivity
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import cylist.api.generated.models.TodoRead
import cylist.api.generated.models.TodoState
import java.util.UUID

/** Test fixtures shaped like the web tests' own (`mentions.test.ts`, `today.test.ts`). */

fun person(name: String, id: UUID = UUID.nameUUIDFromBytes(name.toByteArray())) = PersonRead(
    id = id, name = name, kind = PersonKind.team, title = "Backend engineer", responsibilities = "",
    email = null, colour = "#1D7D46", archivedAt = null, createdAt = "2026-01-01T00:00:00Z",
    isAgent = false, hasAccount = false, inviteIsPending = false,
)

fun file(name: String, folder: String = "", kind: ItemKind = ItemKind.`file`) = FiledItem(
    id = UUID.nameUUIDFromBytes("$folder/$name".toByteArray()), folderId = UUID.nameUUIDFromBytes(folder.toByteArray()),
    kind = kind, name = name, sensitivity = Sensitivity.`internal`,
    url = if (kind == ItemKind.link) "https://drive.example/doc" else null,
    source = if (kind == ItemKind.link) ItemSource.gdrive else ItemSource.upload,
    propertySize = if (kind == ItemKind.link) null else 4096, mime = null, addedBy = null,
    createdAt = "2026-01-01T00:00:00Z", folderPath = folder,
)

fun column(name: String, position: Int) = ColumnRead(
    id = UUID.nameUUIDFromBytes("col-$name".toByteArray()), projectId = UUID(0, 1), name = name,
    description = "", position = position, outcomes = emptyList(), taskCount = 0,
)

val TO_DO = column("To do", 0)
val DOING = column("In progress", 1)
val DONE = column("Done", 2)
val BOARD = listOf(TO_DO, DOING, DONE)

val ME = person("Dhruva")
val ADITI = person("Aditi K")

private var counter = 0

fun card(
    reference: String,
    due: String? = "2026-09-08",
    priority: TaskPriority = TaskPriority.p3,
    status: TaskStatus = TaskStatus.active,
    column: UUID? = TO_DO.id,
    assignee: PersonRead = ME,
): TaskRead {
    counter += 1
    return TaskRead(
        id = UUID.nameUUIDFromBytes(reference.toByteArray()), projectId = UUID(0, 1), reference = reference, number = counter,
        parentId = null, parentReference = null, subNumber = null, columnId = column, position = 0,
        title = reference, description = "", type = TaskType.feature, priority = priority,
        subStatuses = emptyList(), subStatusIndex = null, dueDate = due, columnDueDates = emptyList(), nextDueDate = due,
        assignee = assignee, status = status, templateId = null, templateName = null,
        goalId = null, goalReference = null, goalName = null, goalColour = null, jiraRef = null,
        prRefs = emptyList(), waitingOn = emptyList(), commentCount = 0, checklist = emptyList(), outcome = null, outcomeIndex = null,
        finishedAt = null, openSubtaskCount = 0, subtaskCount = 0, subtaskAssignees = emptyList(),
        createdAt = "2026-01-01T00:00:00Z",
    )
}

fun todo(
    text: String,
    state: TodoState = TodoState.`open`,
    movedTo: String? = null,
    carriedFrom: String? = null,
    source: TodoRead.SourceKind? = null,
    id: UUID = UUID.randomUUID(),
) = TodoRead(
    id = id, text = text, state = state, doneAt = null, movedTo = movedTo, carriedFrom = carriedFrom,
    sourceKind = source, createdAt = "2026-10-16T09:00:00Z",
)

fun pickOf(reference: String, vararg todos: TodoRead, day: String = "2026-10-16") = PickRead(
    id = UUID.nameUUIDFromBytes("pick-$reference".toByteArray()), day = day,
    task = PickedCard(id = UUID.nameUUIDFromBytes(reference.toByteArray()), reference = reference, title = reference, column = "In progress"),
    todos = todos.toList(),
)
