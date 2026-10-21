package cylist.app.task

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentFeedKind
import cylist.api.generated.models.AgentPromptAnswer
import cylist.api.generated.models.AgentPromptKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import cylist.api.generated.models.ChecklistItemRead
import cylist.api.generated.models.ChecklistState
import cylist.api.generated.models.CommentKind
import cylist.api.generated.models.CommentRead
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskDetail
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import cylist.app.board.Avatar
import cylist.app.board.DueKind
import cylist.app.board.dueOf
import cylist.app.board.hex
import cylist.app.board.promptLine
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons
import cylist.app.ui.icon
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.util.UUID

/** The design's few strokes this screen adds to [CylistIcons]. */
private object TaskIcons {
    val Send = icon("send", 24f, 2.1f, "M5 12h14M13 6l6 6-6 6")
    val ChevronRight = icon("chevron-right", 24f, 2.1f, "m9 18 6-6-6-6")
    val Agent = icon("agent", 24f, 2f, "M8 9h8a3 3 0 0 1 3 3v4a3 3 0 0 1-3 3H8a3 3 0 0 1-3-3v-4a3 3 0 0 1 3-3z", "M12 9V6", "M9.5 14h.01M14.5 14h.01")
    val Live = icon("live", 24f, 2.2f, "M4 12h3l2-6 4 12 2-6h5")
}

/**
 * One card, opened from the board: the design's TaskDetail artboard. Back (the
 * arrow, or the system's back) returns to the board.
 */
@Composable
fun TaskDetailScreen(viewModel: TaskDetailViewModel, webUrl: String, close: () -> Unit) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    BackHandler(onBack = close)

    // Held while the screen is started, as the board's is, so an agent's feed
    // and a change made on the web arrive here by themselves.
    LifecycleStartEffect(viewModel) {
        viewModel.connect()
        onStopOrDispose { viewModel.disconnect() }
    }

    TaskDetailContent(
        state = state,
        now = Instant.now(),
        zone = ZoneId.systemDefault(),
        actions = TaskDetailActions(
            close = close,
            refresh = { viewModel.refresh() },
            move = viewModel::move,
            confirmMove = viewModel::confirmMove,
            cancelMove = viewModel::cancelMove,
            toggleItem = viewModel::toggle,
            toggleSubtask = viewModel::toggle,
            setDraft = viewModel::setDraft,
            send = viewModel::sendComment,
            answer = viewModel::answer,
            dismissNotice = viewModel::dismissNotice,
            webUrl = webUrl,
        ),
    )
}

class TaskDetailActions(
    val close: () -> Unit = {},
    val refresh: () -> Unit = {},
    val move: (UUID, String?) -> Unit = { _, _ -> },
    val confirmMove: () -> Unit = {},
    val cancelMove: () -> Unit = {},
    val toggleItem: (ChecklistItemRead) -> Unit = {},
    val toggleSubtask: (TaskRead) -> Unit = {},
    val setDraft: (String) -> Unit = {},
    val send: () -> Unit = {},
    val answer: (AgentPromptRead, AgentPromptAnswer) -> Unit = { _, _ -> },
    val dismissNotice: () -> Unit = {},
    /** The board on the web, narrowed to this card. */
    val webUrl: String = "",
)

/** The screen with nothing behind it but [state], so it can be drawn for a screenshot or a preview. */
@Composable
fun TaskDetailContent(state: TaskDetailState, now: Instant, zone: ZoneId, actions: TaskDetailActions) {
    val colors = CylistTheme.colors
    var moving by rememberSaveable { mutableStateOf(false) }
    val task = state.task

    Column(Modifier.fillMaxSize().background(colors.bg).statusBarsPadding()) {
        Header(state.ref, actions)
        Box(Modifier.weight(1f)) {
            when {
                task != null -> LazyColumn(
                    contentPadding = PaddingValues(start = 18.dp, end = 18.dp, top = 4.dp, bottom = 28.dp),
                    modifier = Modifier.fillMaxSize(),
                ) {
                    card(task, state, now, zone, actions) { moving = true }
                }
                state.error != null -> Message(state.error, actions.refresh)
                else -> Message("Loading ${state.ref}…", null)
            }
        }
        if (task != null) {
            state.notice?.let { Notice(it, actions.dismissNotice) }
            Composer(state, actions)
        }
    }

    if (moving && task != null) {
        MoveSheet(task, state, onPick = { column, outcome ->
            moving = false
            actions.move(column, outcome)
        }, close = { moving = false })
    }
    state.pendingMove?.let { pending ->
        AlertDialog(
            onDismissRequest = actions.cancelMove,
            containerColor = colors.paper,
            title = { Text("Reopen ${state.ref}?", fontFamily = Figtree, fontWeight = FontWeight.Bold, color = colors.ink) },
            text = {
                Text(
                    "It is finished: it is in ${pending.from}, the end of this board. Moving it to ${pending.to} puts it back to work. " +
                        "It stops counting as done, and its history records that it was reopened.",
                    fontFamily = Figtree, color = colors.ink2, fontSize = 15.sp, lineHeight = 22.sp,
                )
            },
            confirmButton = { TextButton(onClick = actions.confirmMove) { Text("Reopen it", color = colors.go, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) } },
            dismissButton = { TextButton(onClick = actions.cancelMove) { Text("Leave it in ${pending.from}", color = colors.ink2, fontFamily = Figtree) } },
        )
    }
}

@Composable
private fun Header(ref: String, actions: TaskDetailActions) {
    val colors = CylistTheme.colors
    var menu by remember { mutableStateOf(false) }
    val uri = LocalUriHandler.current
    Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, top = 14.dp, bottom = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        IconButton(CylistIcons.ChevronLeft, "Back to the board", actions.close)
        Text(ref, color = colors.ink3, fontSize = 13.sp, fontFamily = PlexMono, textAlign = TextAlign.Center, modifier = Modifier.weight(1f))
        Box {
            IconButton(CylistIcons.More, "More actions") { menu = true }
            DropdownMenu(expanded = menu, onDismissRequest = { menu = false }, containerColor = colors.paper) {
                DropdownMenuItem(text = { Text("Refresh", fontFamily = Figtree, color = colors.ink) }, onClick = {
                    menu = false
                    actions.refresh()
                })
                if (actions.webUrl.isNotEmpty()) {
                    DropdownMenuItem(text = { Text("Open on the web", fontFamily = Figtree, color = colors.ink) }, onClick = {
                        menu = false
                        uri.openUri(actions.webUrl)
                    })
                }
            }
        }
    }
}

@Composable
private fun IconButton(icon: androidx.compose.ui.graphics.vector.ImageVector, label: String, onClick: () -> Unit) {
    Box(
        Modifier.size(44.dp).clip(RoundedCornerShape(8.dp)).clickable(role = Role.Button, onClick = onClick).semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = CylistTheme.colors.ink2, modifier = Modifier.size(20.dp))
    }
}

/** Everything under the header, top to bottom as the artboard has it. */
private fun LazyListScope.card(
    task: TaskDetail,
    state: TaskDetailState,
    now: Instant,
    zone: ZoneId,
    actions: TaskDetailActions,
    openMove: () -> Unit,
) {
    item(key = "marks") { Marks(task, LocalDate.now(zone)) }
    item(key = "title") {
        Text(
            task.title, color = CylistTheme.colors.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree,
            lineHeight = 29.sp, letterSpacing = (-0.33).sp, modifier = Modifier.padding(bottom = 16.dp).semantics { heading() },
        )
    }
    val sessions = task.agentSessions.orEmpty()
    if (sessions.isNotEmpty()) {
        items(sessions, key = { "session-${it.id}" }) { session ->
            SessionBox(session, state, now, zone, actions)
        }
    }
    item(key = "fields") { Fields(task, state, now, zone, openMove) }
    item(key = "description") {
        Section("Description")
        Prose(task.description.ifBlank { "No description." }, state, Modifier.padding(bottom = 24.dp))
    }
    if (task.subStatuses.isNotEmpty()) item(key = "stages") { Stages(task) }

    val parts = task.subtasks.size + task.checklist.size
    item(key = "parts-head") {
        val done = task.subtasks.count(::isSettled) + task.checklist.count { it.state != ChecklistState.`open` }
        Section("Sub-tasks", if (parts > 0) "$done/$parts" else null)
        if (parts == 0) Quiet("Nothing to tick off on this card.", Modifier.padding(bottom = 24.dp))
    }
    items(task.subtasks, key = { "sub-${it.id}" }) { sub -> SubtaskRow(sub, it = state, actions) }
    items(task.checklist.sortedBy { it.position }, key = { "item-${it.id}" }) { item -> ChecklistRow(item, state, actions) }
    if (parts > 0) item(key = "parts-foot") { Spacer(Modifier.height(24.dp)) }

    val prs = task.prRefs
    if (task.jiraRef != null || prs.isNotEmpty()) {
        item(key = "linked") {
            Section("Linked")
            Column(Modifier.padding(bottom = 24.dp)) {
                task.jiraRef?.let { LinkRow(CylistIcons.Jira, "Jira", jiraRefDisplay(it), null, CylistTheme.colors.ink3) }
                prs.forEach { pr ->
                    val state = pr.state ?: PrState.raised
                    val tone = when (state) {
                        PrState.merged -> CylistTheme.colors.prMerged
                        PrState.approved -> CylistTheme.colors.prApproved
                        else -> CylistTheme.colors.prRaised
                    }
                    LinkRow(CylistIcons.PullRequest, prRepoOf(pr.url) ?: "Pull request", prRefDisplay(pr.url), state.value, tone)
                }
            }
        }
    }

    if (sessions.isNotEmpty()) {
        item(key = "feed-head") {
            Section(if (sessions.any { it.endedAt == null }) "Agent activity" else "Agent activity, finished", note = "newest first")
            if (state.feed.isEmpty()) Quiet("Nothing yet. Lines appear here as the agent reads, edits, runs and says things.", Modifier.padding(bottom = 24.dp))
        }
        items(state.feed, key = { "feed-${it.id}" }) { FeedLine(it, zone) }
        if (state.feed.isNotEmpty()) item(key = "feed-foot") { Spacer(Modifier.height(24.dp)) }
    }

    val said = task.comments
    item(key = "comments-head") {
        Section("Comments", said.count { it.kind == CommentKind.comment }.takeIf { it > 0 }?.toString())
        if (said.isEmpty()) Quiet("Nothing has been said about this card yet.", Modifier.padding(bottom = 8.dp))
    }
    items(said, key = { "comment-${it.id}" }) { CommentRow(it, state, now, zone) }
}

@Composable
private fun Marks(task: TaskDetail, today: LocalDate) {
    val colors = CylistTheme.colors
    Row(
        Modifier.fillMaxWidth().padding(bottom = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        val (typeTone, weight) = when (task.type) {
            TaskType.bug -> colors.blocked to FontWeight.SemiBold
            TaskType.feature -> colors.ok to FontWeight.Medium
            else -> colors.ink3 to FontWeight.Medium
        }
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            Box(Modifier.width(3.dp).height(14.dp).clip(RoundedCornerShape(2.dp)).background(typeTone))
            Text(task.type.value.replaceFirstChar { it.uppercase() }, color = if (task.type == TaskType.bug) typeTone else colors.ink3, fontSize = 13.sp, fontWeight = weight, fontFamily = Figtree)
        }
        val priority = when (task.priority) {
            TaskPriority.p0 -> Triple(CylistIcons.P0, colors.priorityP0, "Highest priority")
            TaskPriority.p1 -> Triple(CylistIcons.P1, colors.priorityP1, "High priority")
            TaskPriority.p2 -> Triple(CylistIcons.P2, colors.ink3, "Medium priority")
            else -> null
        }
        priority?.let { (icon, tone, name) ->
            Row(Modifier.semantics(mergeDescendants = true) { contentDescription = name }, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                Icon(icon, null, tint = tone, modifier = Modifier.size(12.dp))
                Text(task.priority.value.uppercase(), color = tone, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = PlexMono)
            }
        }
        when (task.status) {
            TaskStatus.blocked -> Text("Blocked", color = colors.blocked, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            TaskStatus.hold -> Text("On hold", color = colors.hold, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            TaskStatus.cancelled -> Text("Cancelled", color = colors.ink3, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            else -> Unit
        }
        dueOf(task.status, task.finishedAt, task.nextDueDate, today)?.let { due ->
            val tone = when (due.kind) {
                DueKind.Late -> colors.blocked
                DueKind.Today, DueKind.Tomorrow -> colors.hold
                else -> colors.ink3
            }
            val where = dueColumnOf(task)?.let { " in $it" }.orEmpty()
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(5.dp)) {
                Icon(if (due.kind == DueKind.Late) CylistIcons.Late else CylistIcons.Clock, null, tint = tone, modifier = Modifier.size(13.dp))
                Text(due.said + where, color = tone, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
    }
}

/** One agent session: who, its state, what it is doing, and what it asks, answerable here. */
@Composable
private fun SessionBox(session: AgentSessionRead, state: TaskDetailState, now: Instant, zone: ZoneId, actions: TaskDetailActions) {
    val colors = CylistTheme.colors
    val pill = sessionPill(session)
    val tone = when (pill.tone) {
        SessionTone.Working -> colors.agent
        SessionTone.Waiting -> colors.hold
        SessionTone.Done -> colors.ok
        SessionTone.Stale -> colors.ink3
    }
    val prompt = session.prompt
    val answerable = isAnswerable(prompt, now)
    Column(
        Modifier
            .fillMaxWidth()
            .padding(bottom = 18.dp)
            .clip(RoundedCornerShape(10.dp))
            .background(colors.paper)
            .border(BorderStroke(1.dp, lerp(colors.line, tone, 0.6f)), RoundedCornerShape(10.dp))
            .padding(start = 14.dp, end = 12.dp, top = 12.dp, bottom = 12.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Column(Modifier.weight(1f).semantics(mergeDescendants = true) {}) {
                Text(pill.label, color = tone, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                Text(
                    "${session.clientName ?: "Agent"} · for ${session.actorLabel} · ${pill.sinceVerb} ${clock(pill.sinceAt, now, zone)} · last seen ${silentFor(session.lastSeenAt, now)} ago",
                    color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree, lineHeight = 17.sp,
                )
            }
        }
        // While it is asking, the box below says what it waits on; a Now line repeating it is noise.
        if (!answerable) {
            nowLine(session)?.let { line ->
                Row(
                    Modifier.fillMaxWidth().semantics(mergeDescendants = true) { liveRegion = LiveRegionMode.Polite },
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    Text("Now", color = colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(top = 1.dp))
                    Text(line, color = colors.ink, fontSize = 14.sp, fontFamily = Figtree, lineHeight = 19.sp, modifier = Modifier.weight(1f))
                }
            }
        }
        when {
            answerable && prompt != null -> PromptAnswer(
                prompt, busy = prompt.id in state.busy, now = now, mine = mayAnswer(prompt, state.me?.id), answer = actions.answer,
            )
            prompt != null -> ClosedPrompt(prompt, state, working = session.state == AgentSessionState.working && session.endedAt == null)
        }
    }
}

/** A question or a permission, its choices as buttons, and for a question a reply in your own words (`AgentPromptAnswer.tsx`). */
@Composable
private fun PromptAnswer(prompt: AgentPromptRead, busy: Boolean, now: Instant, mine: Boolean, answer: (AgentPromptRead, AgentPromptAnswer) -> Unit) {
    val colors = CylistTheme.colors
    var reply by rememberSaveable(prompt.id.toString()) { mutableStateOf("") }
    Column(
        Modifier.fillMaxWidth().padding(top = 4.dp).semantics { liveRegion = LiveRegionMode.Polite },
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text(promptHeading(prompt, now), color = colors.hold, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
        if (prompt.kind == AgentPromptKind.permission) {
            prompt.tool?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono) }
            Text(
                prompt.text, color = colors.ink, fontSize = 13.sp, fontFamily = PlexMono, lineHeight = 18.sp,
                modifier = Modifier.fillMaxWidth().clip(RoundedCornerShape(6.dp)).background(colors.paper2).padding(horizontal = 10.dp, vertical = 8.dp),
            )
        } else {
            Text(prompt.text, color = colors.ink, fontSize = 14.5.sp, fontFamily = Figtree, lineHeight = 21.sp)
        }
        if (!mine) {
            // Asked of someone else: say whose it is rather than offer buttons that can only fail.
            Quiet(notYours(prompt))
            return@Column
        }
        if (prompt.options.isNotEmpty()) {
            val inRow = prompt.kind == AgentPromptKind.permission
            val buttons: @Composable (Modifier) -> Unit = { modifier ->
                prompt.options.forEach { option ->
                    ChoiceButton(option.label, choiceTone(prompt, option.key), enabled = !busy, modifier = modifier) {
                        answer(prompt, AgentPromptAnswer(key = option.key))
                    }
                }
            }
            if (inRow) {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) { buttons(Modifier.weight(1f)) }
            } else {
                Column(verticalArrangement = Arrangement.spacedBy(6.dp)) { buttons(Modifier.fillMaxWidth()) }
            }
        }
        if (prompt.allowsReply) {
            val typed = replyAnswer(prompt, reply)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Field(
                    value = reply,
                    onValueChange = { reply = it },
                    placeholder = if (prompt.options.isEmpty()) "Write a reply…" else "Or write a reply…",
                    label = "Your answer",
                    enabled = !busy,
                    onDone = { typed?.let { answer(prompt, it) } },
                    modifier = Modifier.weight(1f),
                )
                ChoiceButton(if (busy) "Sending…" else "Send", ChoiceTone.Go, enabled = !busy && typed != null) {
                    typed?.let { answer(prompt, it) }
                }
            }
        }
        Text(
            if (prompt.kind == AgentPromptKind.question) "The agent carries on in the same turn once you answer." else "The terminal is asking too; whichever answers first wins.",
            color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree,
        )
    }
}

@Composable
private fun ChoiceButton(label: String, tone: ChoiceTone, enabled: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    val (fill, ink, edge) = when (tone) {
        ChoiceTone.Go -> Triple(colors.go, colors.goInk, colors.go)
        ChoiceTone.Danger -> Triple(colors.paper, colors.blocked, lerp(colors.line2, colors.blocked, 0.5f))
        ChoiceTone.Plain -> Triple(colors.paper, colors.ink, colors.line2)
    }
    Box(
        modifier
            .heightIn(min = 44.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(if (enabled) fill else fill.copy(alpha = 0.5f))
            .border(1.dp, edge, RoundedCornerShape(8.dp))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 8.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(label, color = if (enabled) ink else ink.copy(alpha = 0.6f), fontSize = 13.5.sp, lineHeight = 17.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, textAlign = TextAlign.Center, maxLines = 2)
    }
}

/** The last prompt once it closed: what was answered and by whom, or why it closed without one. */
@Composable
private fun ClosedPrompt(prompt: AgentPromptRead, state: TaskDetailState, working: Boolean) {
    val colors = CylistTheme.colors
    val viewer = state.me?.id
    if (prompt.status == AgentPromptStatus.answered) {
        val who = answeredBy(prompt, viewer)
        Column {
            Text(
                buildAnnotatedString {
                    pushStyle(SpanStyle(fontWeight = FontWeight.SemiBold, color = colors.ok))
                    append(if (who == "Answered") "Answered:" else "$who answered:")
                    pop()
                    append(" ")
                    append(prompt.answer.orEmpty())
                },
                color = colors.ink, fontSize = 13.5.sp, fontFamily = Figtree,
            )
            Text(if (working) "Delivered. The agent is working again." else "Delivered.", color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
        }
    } else {
        promptLine(prompt, viewer)?.let { Quiet(it) }
    }
}

@Composable
private fun Fields(task: TaskDetail, state: TaskDetailState, now: Instant, zone: ZoneId, openMove: () -> Unit) {
    val colors = CylistTheme.colors
    val column = state.columns.firstOrNull { it.id == task.columnId }
    val membersByName = state.members.associateBy { it.name }
    Column(Modifier.fillMaxWidth().padding(bottom = 22.dp)) {
        Rule()
        // The column is where the card is moved from: a tap opens the columns.
        FieldRow(
            "Column",
            onClick = if (task.parentId == null && task.archivedAt == null && !state.moving) openMove else null,
            clickLabel = "Move ${task.reference}",
        ) {
            Box(Modifier.size(7.dp).clip(CircleShape).background(if (task.finishedAt != null) colors.ok else colors.ink2))
            val notes = listOfNotNull(task.outcome, stageNote(task), task.finishedAt?.let { "finished ${ago(it, now, zone)}" })
            Text(
                buildAnnotatedString {
                    append(if (task.archivedAt != null) "Cold storage" else column?.name?.trim() ?: "—")
                    if (notes.isNotEmpty()) {
                        pushStyle(SpanStyle(color = colors.ink3, fontWeight = FontWeight.Normal, fontSize = 13.sp))
                        append(" · " + notes.joinToString(" · "))
                        pop()
                    }
                },
                color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree, modifier = Modifier.weight(1f),
            )
            if (state.moving) Quiet("Moving…") else if (task.parentId == null && task.archivedAt == null) {
                Text("Move", color = colors.link, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                Icon(TaskIcons.ChevronRight, null, tint = colors.ink3, modifier = Modifier.size(16.dp))
            }
        }
        FieldRow("Assignee") {
            Avatar(task.assignee.name, task.assignee.colour, 24.dp, 10.sp)
            Text(task.assignee.name, color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree)
        }
        state.reporter?.let { reporter ->
            FieldRow("Reporter") {
                val known = membersByName[reporter]
                if (known != null) Avatar(known.name, known.colour, 24.dp, 10.sp)
                Text(reporter, color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        if (task.goalName != null) {
            FieldRow("Goal") {
                Box(Modifier.width(3.dp).height(16.dp).clip(RoundedCornerShape(2.dp)).background(hex(task.goalColour, colors.ink3)))
                Text(task.goalName.orEmpty(), color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree)
                task.goalReference?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono) }
            }
        }
        val due = dueOf(task.status, task.finishedAt, task.nextDueDate, LocalDate.now(zone))
        if (due != null || task.dueDate != null) {
            FieldRow("Due") {
                val tone = when (due?.kind) {
                    DueKind.Late -> colors.blocked
                    DueKind.Today, DueKind.Tomorrow -> colors.hold
                    else -> colors.ink
                }
                Text(
                    buildAnnotatedString {
                        append(due?.said ?: task.dueDate?.let { "Was due ${dayOf(it)}" }.orEmpty())
                        dueColumnOf(task)?.let {
                            pushStyle(SpanStyle(color = colors.ink3, fontWeight = FontWeight.Normal, fontSize = 13.sp))
                            append(" in $it")
                            pop()
                        }
                    },
                    color = tone, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree,
                )
            }
        }
        if (task.waitingOn.isNotEmpty()) {
            FieldRow("Waiting on") {
                Text(task.waitingOn.joinToString { it.name }, color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree)
            }
        }
    }
}

@Composable
private fun FieldRow(label: String, onClick: (() -> Unit)? = null, clickLabel: String? = null, value: @Composable androidx.compose.foundation.layout.RowScope.() -> Unit) {
    val colors = CylistTheme.colors
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 46.dp)
                .then(if (onClick != null) Modifier.clickable(role = Role.Button, onClickLabel = clickLabel, onClick = onClick) else Modifier)
                .semantics(mergeDescendants = true) {}
                .padding(vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Text(label, color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree, modifier = Modifier.width(88.dp))
            Row(Modifier.weight(1f), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), content = value)
        }
        Rule()
    }
}

@Composable
private fun Stages(task: TaskDetail) {
    val colors = CylistTheme.colors
    val here = task.subStatusIndex ?: 0
    Section("Stages")
    Row(
        Modifier.fillMaxWidth().padding(top = 2.dp, bottom = 24.dp).semantics(mergeDescendants = true) {
            contentDescription = "Stage ${here + 1} of ${task.subStatuses.size}: ${task.subStatuses.getOrNull(here).orEmpty()}"
        },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        task.subStatuses.forEachIndexed { i, label ->
            if (i > 0) Box(Modifier.padding(horizontal = 6.dp).width(14.dp).height(1.dp).background(colors.line2))
            val dot = when {
                i < here -> colors.ok
                i == here -> colors.ink
                else -> null
            }
            Box(
                Modifier.size(8.dp).clip(CircleShape)
                    .then(if (dot != null) Modifier.background(dot) else Modifier.border(1.5.dp, colors.line2, CircleShape)),
            )
            Text(
                label, color = if (i == here) colors.ink else colors.ink3, fontSize = 13.sp, fontFamily = Figtree,
                fontWeight = if (i == here) FontWeight.SemiBold else FontWeight.Normal, maxLines = 1,
                modifier = Modifier.padding(start = 6.dp),
            )
        }
    }
}

@Composable
private fun SubtaskRow(sub: TaskRead, it: TaskDetailState, actions: TaskDetailActions) {
    val cancelled = sub.status == TaskStatus.cancelled
    val done = sub.finishedAt != null
    TickRow(
        label = sub.title,
        ref = sub.reference,
        ticked = done,
        struck = done || cancelled,
        state = when {
            cancelled -> "cancelled"
            done -> "finished"
            else -> "open"
        },
        enabled = !cancelled && sub.id !in it.busy,
        busy = sub.id in it.busy,
    ) { actions.toggleSubtask(sub) }
}

@Composable
private fun ChecklistRow(item: ChecklistItemRead, state: TaskDetailState, actions: TaskDetailActions) {
    TickRow(
        label = item.title,
        ref = null,
        ticked = item.state == ChecklistState.done,
        struck = item.state != ChecklistState.`open`,
        state = item.state.value,
        enabled = nextChecklistState(item.state) != null && item.id !in state.busy,
        busy = item.id in state.busy,
    ) { actions.toggleItem(item) }
}

@Composable
private fun TickRow(label: String, ref: String?, ticked: Boolean, struck: Boolean, state: String, enabled: Boolean, busy: Boolean, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 44.dp)
                .clickable(enabled = enabled, role = Role.Checkbox, onClick = onClick)
                .semantics(mergeDescendants = true) { stateDescription = if (busy) "saving" else state }
                .padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Box(
                Modifier.size(18.dp).clip(RoundedCornerShape(4.dp))
                    .then(if (ticked) Modifier.background(colors.go) else Modifier.border(1.5.dp, colors.line2, RoundedCornerShape(4.dp))),
                contentAlignment = Alignment.Center,
            ) {
                if (ticked) Icon(CylistIcons.Tick, null, tint = colors.goInk, modifier = Modifier.size(11.dp))
            }
            Text(
                label, color = if (struck) colors.ink3 else colors.ink, fontSize = 14.5.sp, fontFamily = Figtree,
                textDecoration = if (struck) TextDecoration.LineThrough else null, modifier = Modifier.weight(1f),
            )
            if (busy) Quiet("…") else ref?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono) }
        }
        Rule()
    }
}

@Composable
private fun LinkRow(icon: androidx.compose.ui.graphics.vector.ImageVector, what: String, ref: RefDisplay, stateWord: String?, tone: Color) {
    val colors = CylistTheme.colors
    val uri = LocalUriHandler.current
    val href = ref.href
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 46.dp)
                .then(if (href != null) Modifier.clickable(role = Role.Button, onClickLabel = "Open in the browser") { uri.openUri(href) } else Modifier)
                .semantics(mergeDescendants = true) {}
                .padding(vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(icon, null, tint = tone, modifier = Modifier.size(16.dp))
            Text(
                buildAnnotatedString {
                    append("$what ")
                    pushStyle(SpanStyle(fontFamily = PlexMono, fontSize = 12.5.sp, color = if (href != null) colors.link else colors.ink2))
                    append(ref.label)
                    pop()
                },
                color = colors.ink, fontSize = 14.sp, fontFamily = Figtree, modifier = Modifier.weight(1f),
            )
            stateWord?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree) }
        }
        Rule()
    }
}

@Composable
private fun FeedLine(entry: AgentFeedEntryRead, zone: ZoneId) {
    val colors = CylistTheme.colors
    val failed = entry.kind == AgentFeedKind.error
    Column {
        Row(Modifier.fillMaxWidth().padding(vertical = 9.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Text(feedLabel(entry.kind), color = if (failed) colors.blocked else colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.width(44.dp).padding(top = 1.dp))
            Text(
                entry.text,
                color = when {
                    failed -> colors.blocked
                    isCodeLike(entry.kind) -> colors.ink2
                    else -> colors.ink
                },
                fontSize = if (isCodeLike(entry.kind)) 12.5.sp else 14.sp,
                fontFamily = if (isCodeLike(entry.kind)) PlexMono else Figtree,
                lineHeight = 19.sp,
                modifier = Modifier.weight(1f),
            )
            Text(feedClock(entry.at, zone), color = colors.ink3, fontSize = 11.sp, fontFamily = PlexMono, modifier = Modifier.padding(top = 2.dp))
        }
        Rule()
    }
}

@Composable
private fun CommentRow(comment: CommentRead, state: TaskDetailState, now: Instant, zone: ZoneId) {
    val colors = CylistTheme.colors
    val author = comment.author
    if (comment.kind == CommentKind.status_change) {
        Row(Modifier.fillMaxWidth().padding(start = 38.dp, bottom = 14.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                "${author?.name ?: "Cylist"} · ${ago(comment.createdAt, now, zone)} · ${comment.body}",
                color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree, lineHeight = 19.sp,
            )
        }
        return
    }
    Row(Modifier.fillMaxWidth().padding(bottom = 16.dp).semantics(mergeDescendants = true) {}, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (author == null || author.isAgent) {
            Box(
                Modifier.size(28.dp).clip(CircleShape).background(colors.paper2).border(1.dp, colors.line2, CircleShape),
                contentAlignment = Alignment.Center,
            ) {
                Icon(TaskIcons.Agent, "Agent", tint = colors.agent, modifier = Modifier.size(14.dp))
            }
        } else {
            Avatar(author.name, author.colour, 28.dp, 11.sp)
        }
        Column(Modifier.weight(1f)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.padding(bottom = 3.dp)) {
                Text(author?.name ?: "Agent", color = colors.ink, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                Text(ago(comment.createdAt, now, zone), color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
            }
            Prose(comment.body, state, size = 14f)
        }
    }
}

/** Text with each `@Name` of a project member drawn as a tag. */
@Composable
private fun Prose(text: String, state: TaskDetailState, modifier: Modifier = Modifier, size: Float = 15f) {
    val colors = CylistTheme.colors
    val tagged: AnnotatedString = remember(text, state.members) {
        val ranges = mentionRanges(text, state.members.map { it.name })
        buildAnnotatedString {
            var at = 0
            ranges.forEach { range ->
                append(text.substring(at, range.first))
                pushStyle(SpanStyle(color = colors.brandInk, fontWeight = FontWeight.SemiBold))
                append(text.substring(range.first, range.last + 1))
                pop()
                at = range.last + 1
            }
            append(text.substring(at))
        }
    }
    Text(tagged, color = colors.ink2, fontSize = size.sp, lineHeight = (size * 1.55f).sp, fontFamily = Figtree, modifier = modifier)
}

@Composable
private fun Section(title: String, count: String? = null, note: String? = null) {
    val colors = CylistTheme.colors
    Column(Modifier.padding(bottom = 8.dp)) {
        Row(Modifier.padding(bottom = 6.dp).semantics(mergeDescendants = true) { heading() }, verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(title, color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            count?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.Medium, fontFamily = PlexMono) }
            note?.let { Text(it, color = colors.ink3, fontSize = 12.5.sp, fontFamily = Figtree) }
        }
        Rule()
    }
}

@Composable
private fun Rule() = Box(Modifier.fillMaxWidth().height(1.dp).background(CylistTheme.colors.line))

@Composable
private fun Quiet(text: String, modifier: Modifier = Modifier) =
    Text(text, color = CylistTheme.colors.ink3, fontSize = 13.sp, fontFamily = Figtree, lineHeight = 19.sp, modifier = modifier)

@Composable
private fun Message(text: String, retry: (() -> Unit)?) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(text, color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, textAlign = TextAlign.Center)
        if (retry != null) TextButton(onClick = retry) { Text("Try again", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
    }
}

/** Why the last change was refused, in the server's words, until it is dismissed or the next change is made. */
@Composable
private fun Notice(text: String, dismiss: () -> Unit) {
    val colors = CylistTheme.colors
    Row(
        Modifier.fillMaxWidth().background(colors.blockedSoft).padding(start = 16.dp, end = 4.dp, top = 6.dp, bottom = 6.dp)
            .semantics { liveRegion = LiveRegionMode.Assertive },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(text, color = colors.blocked, fontSize = 13.sp, fontFamily = Figtree, lineHeight = 18.sp, modifier = Modifier.weight(1f))
        TextButton(onClick = dismiss) { Text("Dismiss", color = colors.ink2, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
    }
}

/** The comment box, held at the foot of the screen above the keyboard. */
@Composable
private fun Composer(state: TaskDetailState, actions: TaskDetailActions) {
    val colors = CylistTheme.colors
    val ready = state.draft.isNotBlank() && !state.sending
    Column(Modifier.fillMaxWidth().background(colors.paper).imePadding().navigationBarsPadding()) {
        Rule()
        Row(
            Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 10.dp, bottom = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Field(
                value = state.draft,
                onValueChange = actions.setDraft,
                placeholder = "Comment — @ for a person",
                label = "Add a comment",
                enabled = !state.sending,
                onDone = actions.send,
                singleLine = false,
                modifier = Modifier.weight(1f),
            )
            Box(
                Modifier
                    .size(width = 44.dp, height = 42.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(if (ready) colors.go else colors.go.copy(alpha = 0.45f))
                    .clickable(enabled = ready, role = Role.Button, onClick = actions.send)
                    .semantics { contentDescription = if (state.sending) "Sending comment" else "Send comment" },
                contentAlignment = Alignment.Center,
            ) {
                Icon(TaskIcons.Send, null, tint = colors.goInk, modifier = Modifier.size(17.dp))
            }
        }
    }
}

@Composable
private fun Field(
    value: String,
    onValueChange: (String) -> Unit,
    placeholder: String,
    label: String,
    enabled: Boolean,
    onDone: () -> Unit,
    modifier: Modifier = Modifier,
    singleLine: Boolean = true,
) {
    val colors = CylistTheme.colors
    BasicTextField(
        value = value,
        onValueChange = onValueChange,
        enabled = enabled,
        singleLine = singleLine,
        maxLines = if (singleLine) 1 else 5,
        textStyle = TextStyle(color = colors.ink, fontSize = 15.sp, fontFamily = Figtree),
        cursorBrush = SolidColor(colors.ink),
        keyboardOptions = KeyboardOptions(capitalization = KeyboardCapitalization.Sentences, imeAction = if (singleLine) ImeAction.Send else ImeAction.Default),
        keyboardActions = KeyboardActions(onSend = { onDone() }),
        modifier = modifier.semantics { contentDescription = label },
        decorationBox = { inner ->
            Box(
                Modifier
                    .heightIn(min = 42.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .background(colors.paper)
                    .border(1.dp, colors.line2, RoundedCornerShape(8.dp))
                    .padding(horizontal = 12.dp, vertical = 10.dp),
                contentAlignment = Alignment.CenterStart,
            ) {
                if (value.isEmpty()) Text(placeholder, color = colors.ink3, fontSize = 15.sp, fontFamily = Figtree, maxLines = 1)
                inner()
            }
        },
    )
}

/** Where to move the card: every column, and in the last one the outcome it ends on. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun MoveSheet(task: TaskDetail, state: TaskDetailState, onPick: (UUID, String?) -> Unit, close: () -> Unit) {
    val colors = CylistTheme.colors
    val last = state.columns.lastOrNull()
    ModalBottomSheet(onDismissRequest = close, containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, bottom = 24.dp)) {
            Text("Move ${task.reference}", color = colors.ink, fontSize = 18.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree, modifier = Modifier.padding(bottom = 8.dp))
            state.columns.forEach { column ->
                val here = column.id == task.columnId
                val outcomes = outcomesFor(task, column, state.columns, state.templates)
                if (outcomes.isEmpty()) {
                    SheetRow(column.name.trim(), if (here) "here now" else null, selected = here) { onPick(column.id, null) }
                } else {
                    Text(column.name.trim(), color = colors.ink3, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(top = 12.dp, bottom = 2.dp))
                    outcomes.forEach { outcome ->
                        val on = here && outcome.equals(task.outcome, ignoreCase = true)
                        SheetRow(outcome, if (on) "here now" else null, selected = on, indent = true) { onPick(column.id, outcome) }
                    }
                }
            }
            if (last != null && last.id != task.columnId) {
                val open = outstandingWork(task)
                if (open.isNotEmpty()) {
                    Quiet(
                        "${open.size} still open under this card. Every sub-task and checklist item has to be finished or cancelled before it can go to ${last.name.trim()}.",
                        Modifier.padding(top = 10.dp),
                    )
                }
            }
        }
    }
}

@Composable
private fun SheetRow(label: String, note: String?, selected: Boolean, indent: Boolean = false, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .clip(RoundedCornerShape(8.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .padding(start = if (indent) 14.dp else 4.dp, end = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(if (selected) colors.ink else colors.line2))
        Text(label, color = colors.ink, fontSize = 15.sp, fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal, fontFamily = Figtree, modifier = Modifier.weight(1f))
        note?.let { Text(it, color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree) }
    }
}
