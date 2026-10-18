package cylist.app.board

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
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
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.FloatingActionButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
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
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.api.generated.models.AgentPresence
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons
import java.time.LocalDate
import java.util.UUID

/** The Board tab: one project's columns as tabs, and the cards in the chosen one. */
@Composable
fun BoardScreen(viewModel: BoardViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // The socket is held while the screen is started and let go when the app
    // goes to the background; coming back reopens it, and its `ready` refetches.
    LifecycleStartEffect(viewModel) {
        viewModel.connect()
        onStopOrDispose { viewModel.disconnect() }
    }

    BoardContent(
        state = state,
        today = LocalDate.now(),
        actions = BoardActions(
            selectProject = viewModel::selectProject,
            selectColumn = viewModel::selectColumn,
            toggleWho = viewModel::toggleWho,
            showEveryone = viewModel::showEveryone,
            setAgentFilter = viewModel::setAgentFilter,
            addCard = viewModel::addCard,
            retry = { viewModel.refresh() },
        ),
        modifier = modifier,
    )
}

class BoardActions(
    val selectProject: (String) -> Unit = {},
    val selectColumn: (UUID) -> Unit = {},
    val toggleWho: (UUID) -> Unit = {},
    val showEveryone: () -> Unit = {},
    val setAgentFilter: (AgentFilter) -> Unit = {},
    val addCard: (String, String, (String?) -> Unit) -> Unit = { _, _, done -> done(null) },
    val retry: () -> Unit = {},
)

/** The screen with nothing behind it but [state], so previews and screenshots can draw it. */
@Composable
fun BoardContent(state: BoardState, today: LocalDate, actions: BoardActions, modifier: Modifier = Modifier) {
    val colors = CylistTheme.colors
    var adding by rememberSaveable { mutableStateOf(false) }

    val board = state.tasks.filter(::onBoard)
    val narrowed = board.filter { ownedByAny(it, state.who) }
    val working = narrowed.count { it.agentSession?.state == AgentPresence.State.working }
    val needsYou = narrowed.count { it.agentSession?.state == AgentPresence.State.waiting }
    val shown = narrowed.filter { passesAgentFilter(it, state.agentFilter) }
    val column = state.columns.firstOrNull { it.id == state.columnId }
    val isLast = column != null && column.id == state.columns.lastOrNull()?.id
    val inColumn = shown.filter { it.columnId == column?.id }.sortedBy { it.position ?: Int.MAX_VALUE }

    Box(modifier.fillMaxSize().background(colors.bg)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            TopBar(state, actions.selectProject)
            Toolbar(state, board, working, needsYou, actions)
            ColumnTabs(state, shown, actions.selectColumn)
            when {
                state.error != null && state.tasks.isEmpty() -> Message(state.error, actions.retry)
                state.project == null && !state.loading -> Message("No projects yet.", null)
                state.loading && state.tasks.isEmpty() -> Message("Loading the board…", null)
                isLast -> DoneList(inColumn, column.name, state.who.isNotEmpty())
                else -> CardList(inColumn, column?.name.orEmpty(), state, today)
            }
        }
        if (state.project != null) {
            FloatingActionButton(
                onClick = { adding = true },
                containerColor = colors.go,
                contentColor = colors.goInk,
                shape = RoundedCornerShape(16.dp),
                elevation = FloatingActionButtonDefaults.elevation(2.dp, 2.dp, 2.dp, 2.dp),
                modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
            ) {
                Icon(CylistIcons.Plus, contentDescription = "Add a card", modifier = Modifier.size(22.dp))
            }
        }
    }

    if (adding) {
        AddCardSheet(
            projectKey = state.project?.key.orEmpty(),
            firstColumn = state.columns.firstOrNull()?.name.orEmpty(),
            add = actions.addCard,
            close = { adding = false },
        )
    }
}

@Composable
private fun TopBar(state: BoardState, selectProject: (String) -> Unit) {
    val colors = CylistTheme.colors
    var open by remember { mutableStateOf(false) }
    val project = state.project
    Row(
        Modifier.fillMaxWidth().padding(start = 14.dp, end = 8.dp, top = 14.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box {
            Row(
                Modifier
                    .heightIn(min = 44.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(role = Role.Button, enabled = state.projects.size > 1) { open = true }
                    .semantics { contentDescription = "Switch project, now ${project?.key ?: "none"}" }
                    .padding(start = 2.dp, end = 6.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    Modifier.size(24.dp).clip(RoundedCornerShape(6.dp)).background(hex(project?.colour, colors.ink3)),
                    contentAlignment = Alignment.Center,
                ) {
                    Text(project?.key?.take(1).orEmpty(), color = Color.White, fontSize = 12.sp, fontWeight = FontWeight.ExtraBold, fontFamily = Figtree)
                }
                Text(project?.key ?: "Cylist", color = colors.ink, fontSize = 17.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree, letterSpacing = (-0.17).sp)
                Text("Board", color = colors.ink3, fontSize = 15.sp, fontWeight = FontWeight.Medium, fontFamily = Figtree)
                if (state.projects.size > 1) {
                    Icon(CylistIcons.ChevronDown, null, tint = colors.ink3, modifier = Modifier.size(14.dp))
                }
            }
            DropdownMenu(expanded = open, onDismissRequest = { open = false }, containerColor = colors.paper) {
                state.projects.forEach { p ->
                    DropdownMenuItem(
                        text = {
                            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                                Box(Modifier.size(10.dp).clip(RoundedCornerShape(3.dp)).background(hex(p.colour, colors.ink3)))
                                Text(p.key, fontFamily = PlexMono, fontSize = 13.sp, color = colors.ink3)
                                Text(p.name, fontFamily = Figtree, fontSize = 15.sp, color = colors.ink, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        },
                        onClick = {
                            open = false
                            selectProject(p.key)
                        },
                    )
                }
            }
        }
        Spacer(Modifier.weight(1f))
    }
}

@Composable
private fun Toolbar(state: BoardState, board: List<TaskRead>, working: Int, needsYou: Int, actions: BoardActions) {
    val colors = CylistTheme.colors
    var unfolded by rememberSaveable { mutableStateOf(false) }
    val owned = cardsOwned(board)
    val people = facesInOrder(state.members, { it.id }, { it.name }, owned, state.me?.id)
    val byId = people.associateBy { it.id }

    Row(
        Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(start = 12.dp, end = 8.dp, bottom = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (!unfolded) {
            val picked = state.who.mapNotNull { byId[it] }
            val summary = if (picked.isEmpty()) "Showing everyone's cards" else "Showing cards of " + picked.joinToString { it.name }
            Row(
                Modifier
                    .heightIn(min = 44.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(role = Role.Button) { unfolded = true }
                    .semantics { contentDescription = "$summary. Pick whose cards to show" }
                    .padding(horizontal = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                if (picked.isEmpty()) {
                    Text("Everyone", color = colors.ink2, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(end = 2.dp))
                }
                Row {
                    picked.take(3).forEachIndexed { i, p ->
                        Avatar(p.name, p.colour, 24.dp, 10.sp, ring = true, modifier = Modifier.offset(x = (-5 * i).dp))
                    }
                }
                Icon(CylistIcons.ChevronDown, null, tint = colors.ink3, modifier = Modifier.size(16.dp))
            }
        } else {
            Box(
                Modifier.size(width = 28.dp, height = 44.dp).clickable(role = Role.Button) { unfolded = false }
                    .semantics { contentDescription = "Fold the faces" },
                contentAlignment = Alignment.Center,
            ) {
                Icon(CylistIcons.ChevronLeft, null, tint = colors.ink3, modifier = Modifier.size(16.dp))
            }
            Row(
                Modifier.weight(1f, fill = false).horizontalScroll(rememberScrollState()),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                val nobody = state.who.isEmpty()
                Text(
                    "All",
                    color = if (nobody) colors.ink else colors.ink3,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    fontFamily = Figtree,
                    modifier = Modifier
                        .heightIn(min = 44.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .clickable(role = Role.Button) { actions.showEveryone() }
                        .semantics { contentDescription = "Show everyone's cards" }
                        .padding(horizontal = 8.dp, vertical = 13.dp),
                )
                people.forEach { p ->
                    val on = p.id in state.who
                    val mine = if (p.id == state.me?.id) "You, " else ""
                    Box(
                        Modifier
                            .size(width = 40.dp, height = 44.dp)
                            .clickable(role = Role.Checkbox) { actions.toggleWho(p.id) }
                            .semantics { contentDescription = "$mine${p.name}, ${owned[p.id] ?: 0} cards" + if (on) ", shown" else "" },
                        contentAlignment = Alignment.Center,
                    ) {
                        Avatar(
                            p.name, p.colour, 32.dp, 12.sp, ring = true, selected = on, dim = !on && !nobody,
                        )
                    }
                }
            }
        }
        // While the faces are open they need the width; the counts come back when they fold.
        if (!unfolded && (working + needsYou > 0 || state.agentFilter != AgentFilter.All)) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp), verticalAlignment = Alignment.CenterVertically) {
                if (working > 0 || state.agentFilter == AgentFilter.Working) {
                    AgentCount(working, "working", colors.agent, state.agentFilter == AgentFilter.Working) { actions.setAgentFilter(AgentFilter.Working) }
                }
                if (needsYou > 0 || state.agentFilter == AgentFilter.NeedsYou) {
                    AgentCount(needsYou, "need you", colors.hold, state.agentFilter == AgentFilter.NeedsYou) { actions.setAgentFilter(AgentFilter.NeedsYou) }
                }
            }
        }
        if (!unfolded) Spacer(Modifier.weight(1f))
    }
}

@Composable
private fun AgentCount(count: Int, label: String, dot: Color, on: Boolean, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Row(
        Modifier.heightIn(min = 32.dp).clip(RoundedCornerShape(6.dp)).clickable(role = Role.Switch, onClick = onClick).padding(horizontal = 2.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(dot))
        Text(count.toString(), color = colors.ink, fontSize = 12.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
        Text(label, color = if (on) colors.ink else colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
    }
}

@Composable
private fun ColumnTabs(state: BoardState, shown: List<TaskRead>, select: (UUID) -> Unit) {
    val colors = CylistTheme.colors
    Box(Modifier.fillMaxWidth()) {
        Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(1.dp).background(colors.line))
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            state.columns.forEach { column ->
                val on = column.id == state.columnId
                val count = shown.count { it.columnId == column.id && !isHeld(it) }
                Box(
                    Modifier
                        .heightIn(min = 44.dp)
                        .clickable(role = Role.Tab) { select(column.id) }
                        .semantics { contentDescription = "${column.name.trim()}, $count cards" + if (on) ", selected" else "" },
                ) {
                    Row(
                        Modifier.align(Alignment.Center).padding(horizontal = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        Text(column.name.trim(), color = if (on) colors.ink else colors.ink3, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1)
                        Text(count.toString(), color = colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.Medium, fontFamily = PlexMono)
                    }
                    if (on) {
                        Box(
                            Modifier.align(Alignment.BottomCenter).padding(horizontal = 10.dp).fillMaxWidth().height(2.dp)
                                .clip(RoundedCornerShape(2.dp)).background(colors.ink),
                        )
                    }
                }
            }
        }
    }
}

@Composable
private fun CardList(cards: List<TaskRead>, columnName: String, state: BoardState, today: LocalDate) {
    val colors = CylistTheme.colors
    val (held, open) = cards.partition(::isHeld)
    var showHeld by rememberSaveable(state.columnId) { mutableStateOf(false) }
    LazyColumn(
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 88.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        if (open.isEmpty() && held.isEmpty()) {
            item { EmptyText(columnName.trim(), state.who.isNotEmpty()) }
        }
        items(open, key = { it.id }) { TaskCard(it, today, state.me?.id) }
        if (held.isNotEmpty()) {
            item(key = "held") {
                Text(
                    if (showHeld) "Hide ${held.size} on hold" else "${held.size} on hold",
                    color = colors.ink3,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    fontFamily = Figtree,
                    modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(RoundedCornerShape(8.dp))
                        .clickable(role = Role.Button) { showHeld = !showHeld }.padding(horizontal = 4.dp, vertical = 13.dp),
                )
            }
            if (showHeld) items(held, key = { it.id }) { TaskCard(it, today, state.me?.id) }
        }
    }
}

@Composable
private fun DoneList(cards: List<TaskRead>, columnName: String, narrowed: Boolean) {
    val colors = CylistTheme.colors
    val finished = cards.sortedByDescending { it.finishedAt.orEmpty() }
    LazyColumn(contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 88.dp), modifier = Modifier.fillMaxSize()) {
        if (finished.isEmpty()) item { EmptyText(columnName.trim(), narrowed) }
        items(finished, key = { it.id }) { task ->
            Column {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(horizontal = 4.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Icon(CylistIcons.Tick, null, tint = colors.ok, modifier = Modifier.width(20.dp).size(15.dp))
                    Text(
                        task.title, color = colors.ink3, fontSize = 14.sp, fontFamily = Figtree, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        textDecoration = TextDecoration.LineThrough, modifier = Modifier.weight(1f),
                    )
                    Text(task.reference, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
            }
        }
    }
}

@Composable
private fun EmptyText(columnName: String, narrowed: Boolean) {
    Text(
        if (narrowed) "Nobody picked owns a card in $columnName." else "Nothing in $columnName.",
        color = CylistTheme.colors.ink2,
        fontSize = 14.sp,
        fontFamily = Figtree,
        lineHeight = 21.sp,
        textAlign = TextAlign.Center,
        modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 28.dp),
    )
}

/** One card, as the design's iteration B draws it. */
@Composable
fun TaskCard(task: TaskRead, today: LocalDate, viewerId: UUID?) {
    val colors = CylistTheme.colors
    val agent = agentLineOf(task.agentSession, viewerId)
    val blocked = task.status == TaskStatus.blocked
    val border = when {
        agent?.waiting == true -> lerp(colors.line, colors.hold, 0.6f)
        agent != null -> lerp(colors.line, colors.agent, 0.6f)
        blocked -> lerp(colors.line, colors.blocked, 0.45f)
        else -> colors.line
    }
    val rail = hexOrNull(task.goalColour) ?: when {
        blocked -> colors.blocked
        task.type == TaskType.feature -> colors.ok
        task.type == TaskType.bug -> colors.blocked
        else -> colors.line2
    }
    val due = dueOf(task, today)

    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(colors.paper)
            .border(BorderStroke(1.dp, border), RoundedCornerShape(10.dp))
            // The rail: the goal's colour down the left edge. An agent's card
            // wears its border instead, so the two never say different things.
            .drawBehind {
                if (agent == null) {
                    val inset = 6.dp.toPx()
                    drawRoundRect(
                        rail.copy(alpha = 0.85f),
                        topLeft = Offset(0f, inset),
                        size = Size(3.dp.toPx(), size.height - 2 * inset),
                        cornerRadius = CornerRadius(3.dp.toPx()),
                    )
                }
            }
            .semantics(mergeDescendants = true) {},
    ) {
        Column(
            Modifier.fillMaxWidth().padding(start = 16.dp, end = 14.dp, top = 12.dp, bottom = 11.dp),
            verticalArrangement = Arrangement.spacedBy(9.dp),
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(
                    task.title, color = colors.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree,
                    lineHeight = 21.sp, letterSpacing = (-0.075).sp, modifier = Modifier.weight(1f),
                )
                PriorityMark(task.priority)
            }
            if (agent != null) {
                val tone = if (agent.waiting) colors.hold else colors.agent
                Row(
                    Modifier.fillMaxWidth().drawLeftRule(tone).padding(start = 9.dp, top = 1.dp, bottom = 1.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    Text(agent.label, color = tone, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = agent.detail == null))
                    agent.detail?.let {
                        Text(it, color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    }
                }
            } else {
                Progress(task)
            }
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(task.reference, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
                if (blocked) Mark(CylistIcons.Blocked, "Blocked", colors.blocked, FontWeight.SemiBold)
                if (due != null) {
                    val (icon, tone, weight) = when (due.kind) {
                        DueKind.Late -> Triple(CylistIcons.Late, colors.blocked, FontWeight.SemiBold)
                        DueKind.Today -> Triple(CylistIcons.Clock, colors.hold, FontWeight.SemiBold)
                        DueKind.Tomorrow -> Triple(CylistIcons.Calendar, colors.hold, FontWeight.SemiBold)
                        DueKind.Soon -> Triple(CylistIcons.Calendar, colors.ink2, FontWeight.Medium)
                        DueKind.Later -> Triple(CylistIcons.Calendar, colors.ink3, FontWeight.Medium)
                    }
                    Mark(icon, due.text, tone, weight, description = due.said)
                }
                if (task.jiraRef != null) {
                    Icon(CylistIcons.Jira, "Jira ${task.jiraRef}", tint = colors.ink3, modifier = Modifier.size(13.dp))
                }
                prStateOf(task)?.let { pr ->
                    val tone = when (pr) {
                        PrState.merged -> colors.prMerged
                        PrState.approved -> colors.prApproved
                        else -> colors.prRaised
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                        Icon(CylistIcons.PullRequest, "Pull request, ${pr.value}", tint = tone, modifier = Modifier.size(13.dp))
                        if (task.prRefs.size > 1) Text("×${task.prRefs.size}", color = tone, fontSize = 11.sp, fontFamily = PlexMono)
                    }
                }
                Spacer(Modifier.weight(1f))
                Avatar(task.assignee.name, task.assignee.colour, 22.dp, 10.sp)
            }
        }
    }
}

@Composable
private fun Progress(task: TaskRead) {
    val colors = CylistTheme.colors
    val stages = task.subStatuses
    val stage = task.subStatusIndex
    val staged = stages.isNotEmpty() && stage != null
    if (!staged && task.subtaskCount == 0) return
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(14.dp)) {
        if (staged) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.weight(1f, fill = false)) {
                Row(Modifier.width(52.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                    stages.indices.forEach { i ->
                        val tone = when {
                            i < stage -> colors.ok
                            i == stage -> colors.ink2
                            else -> colors.line2
                        }
                        Box(Modifier.weight(1f).height(3.dp).clip(RoundedCornerShape(3.dp)).background(tone))
                    }
                }
                Text(stages[stage], color = colors.ink2, fontSize = 12.sp, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f, fill = false))
                Text("${stage + 1}/${stages.size}", color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
            }
        }
        if (task.subtaskCount > 0) {
            val finished = task.subtaskCount - task.openSubtaskCount
            Row(
                Modifier.semantics(mergeDescendants = true) { contentDescription = "$finished of ${task.subtaskCount} sub-tasks finished" },
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(4.dp),
            ) {
                Icon(CylistIcons.Tick, null, tint = colors.ink3, modifier = Modifier.size(13.dp))
                Text("$finished/${task.subtaskCount}", color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
            }
        }
    }
}

@Composable
private fun PriorityMark(priority: TaskPriority) {
    val colors = CylistTheme.colors
    val (icon, tone, name) = when (priority) {
        TaskPriority.p0 -> Triple(CylistIcons.P0, colors.priorityP0, "Highest priority")
        TaskPriority.p1 -> Triple(CylistIcons.P1, colors.priorityP1, "High priority")
        TaskPriority.p2 -> Triple(CylistIcons.P2, colors.ink3, "Medium priority")
        // P3 is the baseline every card starts on; flagging it would be noise on every card.
        else -> return
    }
    Row(
        Modifier.padding(top = 3.dp).semantics(mergeDescendants = true) { contentDescription = name },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        Icon(icon, null, tint = tone, modifier = Modifier.size(12.dp))
        Text(priority.value.uppercase(), color = tone, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = PlexMono)
    }
}

@Composable
private fun Mark(icon: ImageVector, text: String, tone: Color, weight: FontWeight, description: String = text) {
    Row(
        Modifier.semantics(mergeDescendants = true) { contentDescription = description },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Icon(icon, null, tint = tone, modifier = Modifier.size(13.dp))
        Text(text, color = tone, fontSize = 12.sp, fontWeight = weight, fontFamily = Figtree, maxLines = 1)
    }
}

@Composable
private fun Avatar(
    name: String,
    colour: String,
    size: Dp,
    textSize: TextUnit,
    modifier: Modifier = Modifier,
    ring: Boolean = false,
    selected: Boolean = false,
    dim: Boolean = false,
) {
    val colors = CylistTheme.colors
    val ringed = when {
        selected -> Modifier.border(2.dp, colors.ink, CircleShape).padding(2.dp).border(2.dp, colors.bg, CircleShape)
        ring -> Modifier.border(2.dp, colors.bg, CircleShape)
        else -> Modifier
    }
    Box(
        modifier.size(size).then(ringed).clip(CircleShape).background(hex(colour, colors.ink3).copy(alpha = if (dim) 0.45f else 1f)),
        contentAlignment = Alignment.Center,
    ) {
        Text(initialsOf(name), color = Color.White.copy(alpha = if (dim) 0.8f else 1f), fontSize = textSize, fontWeight = FontWeight.Bold, fontFamily = Figtree)
    }
}

@Composable
private fun Message(text: String, retry: (() -> Unit)?) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(text, color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, textAlign = TextAlign.Center)
        if (retry != null) TextButton(onClick = retry) { Text("Try again", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AddCardSheet(projectKey: String, firstColumn: String, add: (String, String, (String?) -> Unit) -> Unit, close: () -> Unit) {
    val colors = CylistTheme.colors
    var title by rememberSaveable { mutableStateOf("") }
    var description by rememberSaveable { mutableStateOf("") }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    ModalBottomSheet(onDismissRequest = close, containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().imePadding().padding(start = 20.dp, end = 20.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
            Text("New card on $projectKey", color = colors.ink, fontSize = 18.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
            OutlinedTextField(
                value = title,
                onValueChange = { title = it },
                label = { Text("Title", fontFamily = Figtree) },
                singleLine = false,
                maxLines = 4,
                colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = colors.ink, unfocusedBorderColor = colors.line2, cursorColor = colors.ink),
                modifier = Modifier.fillMaxWidth(),
            )
            OutlinedTextField(
                value = description,
                onValueChange = { description = it },
                label = { Text("Description", fontFamily = Figtree) },
                minLines = 2,
                maxLines = 6,
                colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = colors.ink, unfocusedBorderColor = colors.line2, cursorColor = colors.ink),
                modifier = Modifier.fillMaxWidth(),
            )
            Text("It goes into $firstColumn, assigned to you.", color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree)
            error?.let { Text(it, color = colors.blocked, fontSize = 13.sp, fontFamily = Figtree) }
            Button(
                onClick = {
                    busy = true
                    error = null
                    add(title, description) { failed ->
                        busy = false
                        if (failed == null) close() else error = failed
                    }
                },
                enabled = title.isNotBlank() && description.isNotBlank() && !busy,
                colors = ButtonDefaults.buttonColors(containerColor = colors.go, contentColor = colors.goInk),
                shape = RoundedCornerShape(8.dp),
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
            ) {
                Text(if (busy) "Adding…" else "Add card", fontFamily = Figtree, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            }
        }
    }
}

/** The agent line's 2dp rule down its left edge. */
private fun Modifier.drawLeftRule(color: Color): Modifier = drawBehind {
    drawRect(color, size = Size(2.dp.toPx(), size.height))
}

/** "#4A7B8C" as a colour, or null when it is missing or not one. */
private fun hexOrNull(value: String?): Color? =
    value?.removePrefix("#")?.takeIf { it.length == 6 }?.toLongOrNull(16)?.let { Color(0xFF000000 or it) }

private fun hex(value: String?, fallback: Color): Color = hexOrNull(value) ?: fallback
