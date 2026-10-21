package cylist.app.board

import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.core.Spring
import androidx.compose.animation.core.animate
import androidx.compose.animation.core.spring
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInHorizontally
import androidx.compose.animation.slideOutHorizontally
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.awaitEachGesture
import androidx.compose.foundation.gestures.awaitFirstDown
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
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
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
import androidx.compose.material3.Snackbar
import androidx.compose.material3.SnackbarDuration
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.SnackbarResult
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableFloatStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.rememberUpdatedState
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.PointerEventPass
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.input.pointer.util.VelocityTracker
import androidx.compose.ui.input.pointer.util.addPointerInputChange
import androidx.compose.ui.layout.layout
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.api.generated.models.AgentPresence
import cylist.api.generated.models.ColumnRead
import cylist.api.generated.models.MemberRead
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.PrState
import cylist.api.generated.models.TaskPriority
import cylist.api.generated.models.TaskSummary
import cylist.api.generated.models.TaskStatus
import cylist.api.generated.models.TaskType
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons
import java.time.LocalDate
import java.util.UUID
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withTimeoutOrNull

/** The Board tab: one project's columns as tabs, and the cards in the chosen one. */
@Composable
fun BoardScreen(viewModel: BoardViewModel, modifier: Modifier = Modifier, openTask: (projectKey: String, ref: String) -> Unit = { _, _ -> }) {
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
            reassign = viewModel::reassign,
            undo = viewModel::undo,
            dismissNotice = viewModel::dismissNotice,
            retry = { viewModel.refresh() },
            openTask = { ref -> state.project?.key?.let { openTask(it, ref) } },
            loadMore = viewModel::loadMore,
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
    /** Give a card to someone else. */
    val reassign: (TaskSummary, MemberRead) -> Unit = { _, _ -> },
    /** Take back the reassignment a snackbar reported. */
    val undo: (BoardNotice) -> Unit = {},
    val dismissNotice: (Long) -> Unit = {},
    val retry: () -> Unit = {},
    /** Open a card's detail, by its reference. */
    val openTask: (String) -> Unit = {},
    /** Read the next page of a column onto the bottom of it. */
    val loadMore: (UUID) -> Unit = {},
)

/** The screen with nothing behind it but [state], so previews and screenshots can draw it. */
@Composable
fun BoardContent(state: BoardState, today: LocalDate, actions: BoardActions, modifier: Modifier = Modifier) {
    val colors = CylistTheme.colors
    var adding by rememberSaveable { mutableStateOf(false) }
    var assigning by rememberSaveable { mutableStateOf<String?>(null) }

    val board = state.tasks.filter(::onBoard)
    val narrowed = board.filter { ownedByAny(it, state.who) }
    val working = narrowed.count { it.agentSession?.state == AgentPresence.State.working }
    val needsYou = narrowed.count { it.agentSession?.state == AgentPresence.State.waiting }
    // Every column's page is drawn from the same filtered cards, so a swipe
    // never shows a column the people or agent filters have not narrowed.
    val shown = narrowed.filter { passesAgentFilter(it, state.agentFilter) }
    val counts = state.columns.associate { column -> column.id to shown.count { it.columnId == column.id && !isHeld(it) } }
    val lastId = state.columns.lastOrNull()?.id

    Box(modifier.fillMaxSize().background(colors.bg)) {
        Column(Modifier.fillMaxSize().statusBarsPadding()) {
            TopBar(state, actions.selectProject)
            Toolbar(state, board, working, needsYou, actions)
            ColumnTabs(state, counts, actions.selectColumn)
            when {
                state.error != null && state.tasks.isEmpty() -> Message(state.error, actions.retry)
                state.project == null && !state.loading -> Message("No projects yet.", null)
                state.loading && state.tasks.isEmpty() -> Message("Loading the board…", null)
                else -> SwipeColumns(state.columns, state.columnId, counts, actions.selectColumn) { column ->
                    val inColumn = shown.filter { it.columnId == column.id }.sortedBy { it.position ?: Int.MAX_VALUE }
                    val more = unloadedIn(state, column.id)
                    val loadMore = { actions.loadMore(column.id) }
                    if (column.id == lastId) {
                        DoneList(inColumn, column.name, state.who.isNotEmpty(), more, loadMore, actions.openTask)
                    } else {
                        CardList(inColumn, column.id, column.name, state, today, more, loadMore, actions.openTask) { assigning = it.reference }
                    }
                }
            }
        }
        NoticeBar(state.notice, actions, Modifier.align(Alignment.BottomCenter).padding(start = 12.dp, end = 12.dp, bottom = 84.dp))
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

    state.tasks.firstOrNull { it.reference == assigning }?.let { task ->
        AssignSheet(
            task = task,
            state = state,
            pick = { person -> actions.reassign(task, person) },
            close = { assigning = null },
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
private fun Toolbar(state: BoardState, board: List<TaskSummary>, working: Int, needsYou: Int, actions: BoardActions) {
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
private fun ColumnTabs(state: BoardState, counts: Map<UUID, Int>, select: (UUID) -> Unit) {
    val colors = CylistTheme.colors
    Box(Modifier.fillMaxWidth()) {
        Box(Modifier.align(Alignment.BottomStart).fillMaxWidth().height(1.dp).background(colors.line))
        Row(Modifier.horizontalScroll(rememberScrollState()).padding(horizontal = 10.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
            state.columns.forEach { column ->
                val on = column.id == state.columnId
                val count = counts[column.id] ?: 0
                // A swipe can land on a tab scrolled out of sight; bring it back in.
                val seen = remember { BringIntoViewRequester() }
                LaunchedEffect(on) { if (on) seen.bringIntoView() }
                Box(
                    Modifier
                        .bringIntoViewRequester(seen)
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
private fun CardList(
    cards: List<TaskSummary>,
    columnId: UUID,
    columnName: String,
    state: BoardState,
    today: LocalDate,
    more: Int,
    loadMore: () -> Unit,
    openTask: (String) -> Unit,
    assign: (TaskSummary) -> Unit,
) {
    val colors = CylistTheme.colors
    val (held, open) = cards.partition(::isHeld)
    var showHeld by rememberSaveable(columnId) { mutableStateOf(false) }
    LazyColumn(
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 88.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
        modifier = Modifier.fillMaxSize(),
    ) {
        if (open.isEmpty() && held.isEmpty()) {
            item { EmptyText(columnName.trim(), state.who.isNotEmpty()) }
        }
        items(open, key = { it.id }) { TaskCard(it, today, state.me?.id, onAssign = { assign(it) }) { openTask(it.reference) } }
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
            if (showHeld) items(held, key = { it.id }) { TaskCard(it, today, state.me?.id, onAssign = { assign(it) }) { openTask(it.reference) } }
        }
        if (more > 0) item(key = "more") { LoadMore(more, loadMore) }
    }
}

/**
 * How many of a column's cards are not on the phone yet: the board arrives a
 * page per column, and a long column's tail is read when somebody asks for it.
 */
private fun unloadedIn(state: BoardState, columnId: UUID): Int {
    val stack = state.stacks[columnId] ?: return 0
    if (stack.next == null) return 0
    return (stack.total - state.tasks.count { it.columnId == columnId }).coerceAtLeast(1)
}

@Composable
private fun LoadMore(more: Int, load: () -> Unit) {
    Text(
        "Show more · $more not loaded",
        color = CylistTheme.colors.ink3,
        fontSize = 13.sp,
        fontWeight = FontWeight.SemiBold,
        fontFamily = Figtree,
        modifier = Modifier.fillMaxWidth().heightIn(min = 44.dp).clip(RoundedCornerShape(8.dp))
            .clickable(role = Role.Button, onClick = load).padding(horizontal = 4.dp, vertical = 13.dp),
    )
}

@Composable
private fun DoneList(cards: List<TaskSummary>, columnName: String, narrowed: Boolean, more: Int, loadMore: () -> Unit, open: (String) -> Unit) {
    val colors = CylistTheme.colors
    val finished = cards.sortedByDescending { it.finishedAt.orEmpty() }
    LazyColumn(contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 88.dp), modifier = Modifier.fillMaxSize()) {
        if (finished.isEmpty()) item { EmptyText(columnName.trim(), narrowed) }
        items(finished, key = { it.id }) { task ->
            Column {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 48.dp).clickable(role = Role.Button) { open(task.reference) }.padding(horizontal = 4.dp, vertical = 6.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    Icon(CylistIcons.Tick, null, tint = colors.ok, modifier = Modifier.width(20.dp).size(15.dp))
                    Text(
                        task.title, color = colors.ink3, fontSize = 14.sp, fontFamily = Figtree, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        textDecoration = TextDecoration.LineThrough, modifier = Modifier.weight(1f),
                    )
                    Text(task.reference, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
                    Avatar(
                        task.assignee.name, task.assignee.colour, 22.dp, 10.sp,
                        modifier = Modifier.alpha(0.75f).semantics { contentDescription = "Assigned to ${task.assignee.name}" },
                    )
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
            }
        }
        if (more > 0) item(key = "more") { LoadMore(more, loadMore) }
    }
}

/**
 * The card list, one column at a time, swiped sideways between columns. The
 * list follows the finger and, let go, either moves to the neighbouring
 * column or springs back ([swipeStep]); the first and last columns rubber-band
 * ([swipeFollow]). A gesture is only taken once it is clearly sideways
 * ([swipeAxisOf]), so anything else is the list's to scroll.
 *
 * The gesture is read on the Initial pass, before the cards see it, and each
 * change is consumed from the moment it is sideways: the card it started on
 * sees its press cancelled and does not open, and the list does not scroll.
 *
 * A change of column, by swipe or by tab, slides the new list in 40dp from
 * the side it comes from while it fades in.
 */
@Composable
private fun SwipeColumns(
    columns: List<ColumnRead>,
    columnId: UUID?,
    counts: Map<UUID, Int>,
    select: (UUID) -> Unit,
    page: @Composable (ColumnRead) -> Unit,
) {
    val index = columns.indexOfFirst { it.id == columnId }.coerceAtLeast(0)
    val current = columns.getOrNull(index) ?: return
    val previous = columns.getOrNull(index - 1)
    val next = columns.getOrNull(index + 1)
    val density = LocalDensity.current
    val scope = rememberCoroutineScope()
    val pick by rememberUpdatedState(select)

    // How far a list is drawn off centre, and which column's list that is. A
    // list swiped away keeps its offset while it fades out.
    var offset by remember { mutableFloatStateOf(0f) }
    var offsetOf by remember { mutableStateOf<UUID?>(null) }
    var springing by remember { mutableStateOf<Job?>(null) }
    LaunchedEffect(current.id) {
        delay(SWITCH_MS.toLong())
        if (offsetOf != current.id) {
            offsetOf = null
            offset = 0f
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .clipToBounds()
            .pointerInput(current.id, previous?.id, next?.id) {
                fun release(dx: Float, velocity: Float) {
                    val step = swipeStep(dx.toDp().value, (velocity / 1000f).toDp().value, previous != null, next != null)
                    val target = columns.getOrNull(index + step)
                    if (step != 0 && target != null) {
                        pick(target.id)
                    } else {
                        springing = scope.launch { animate(offset, 0f, animationSpec = spring(stiffness = Spring.StiffnessMediumLow)) { v, _ -> offset = v } }
                    }
                }
                awaitEachGesture {
                    val down = awaitFirstDown(requireUnconsumed = false, pass = PointerEventPass.Initial)
                    val tracker = VelocityTracker().apply { addPointerInputChange(down) }
                    var sideways = false
                    var dx = 0f
                    while (true) {
                        val change = awaitPointerEvent(PointerEventPass.Initial).changes.firstOrNull { it.id == down.id } ?: break
                        tracker.addPointerInputChange(change)
                        val moved = change.position - down.position
                        if (!sideways) {
                            if (!change.pressed) break
                            when (swipeAxisOf(moved.x.toDp().value, moved.y.toDp().value)) {
                                null -> continue
                                SwipeAxis.UpDown -> break
                                SwipeAxis.Sideways -> {
                                    sideways = true
                                    springing?.cancel()
                                    offsetOf = current.id
                                }
                            }
                        }
                        change.consume()
                        if (!change.pressed) break
                        dx = moved.x
                        offset = swipeFollow(dx, previous != null, next != null)
                    }
                    if (sideways) release(dx, tracker.calculateVelocity().x)
                }
            },
    ) {
        AnimatedContent(
            targetState = current.id,
            transitionSpec = {
                val from = columns.indexOfFirst { it.id == initialState }
                val to = columns.indexOfFirst { it.id == targetState }
                val side = if (to >= from) 1 else -1
                val shift = with(density) { SWITCH_SHIFT.roundToPx() }
                (slideInHorizontally(tween(SWITCH_MS)) { side * shift } + fadeIn(tween(SWITCH_MS)))
                    .togetherWith(slideOutHorizontally(tween(SWITCH_MS)) { -side * shift } + fadeOut(tween(SWITCH_MS)))
            },
            label = "column",
        ) { id ->
            val column = columns.firstOrNull { it.id == id }
            Box(Modifier.fillMaxSize().graphicsLayer { translationX = if (offsetOf == id) offset else 0f }) {
                if (column != null) page(column)
            }
        }
        // Over the list rather than behind it: a column's name is often wider
        // than the gap the drag has opened.
        val drawn = if (offsetOf == current.id) offset else 0f
        val reach = drawn / with(density) { SWIPE_COMMIT_DP.dp.toPx() }
        if (drawn > 0f && previous != null) {
            EdgeHint("‹ ${previous.name.trim()}", counts[previous.id] ?: 0, reach, Modifier.align(Alignment.CenterStart))
        }
        if (drawn < 0f && next != null) {
            EdgeHint(next.name.trim(), counts[next.id] ?: 0, -reach, Modifier.align(Alignment.CenterEnd), trailing = " ›")
        }
    }
}

/**
 * The neighbouring column's name and count, fading in at the edge the list is
 * leaving, and darkening once the drag is far enough to go there.
 */
@Composable
private fun EdgeHint(name: String, count: Int, reach: Float, modifier: Modifier, trailing: String = "") {
    val colors = CylistTheme.colors
    val tone = if (reach >= 1f) colors.ink else colors.ink3
    Row(
        modifier
            .padding(horizontal = 8.dp)
            .alpha(reach.coerceIn(0f, 1f))
            .clip(RoundedCornerShape(16.dp))
            .background(colors.paper)
            .border(1.dp, colors.line, RoundedCornerShape(16.dp))
            .padding(horizontal = 12.dp, vertical = 7.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(5.dp),
    ) {
        Text(name, color = tone, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1)
        Text(count.toString() + trailing, color = tone, fontSize = 12.sp, fontWeight = FontWeight.Medium, fontFamily = PlexMono)
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
fun TaskCard(task: TaskSummary, today: LocalDate, viewerId: UUID?, onAssign: (() -> Unit)? = null, onOpen: (() -> Unit)? = null) {
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
            .then(if (onOpen != null) Modifier.clickable(role = Role.Button, onClickLabel = "Open ${task.reference}", onClick = onOpen) else Modifier)
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
                AssigneeChip(task.assignee, viewerId, onAssign)
            }
        }
    }
}

/**
 * Who a card is assigned to: their avatar and first name, "You" for the
 * viewer. With [onAssign] it is its own touch target, 40dp tall, apart from
 * the card's, and opens the assign sheet.
 */
@Composable
private fun AssigneeChip(person: PersonRead, viewerId: UUID?, onAssign: (() -> Unit)?) {
    val colors = CylistTheme.colors
    val label = assigneeLabel(person.id, person.name, viewerId)
    val said = if (label == "You") "Assigned to you" else "Assigned to ${person.name}"
    Row(
        Modifier
            // Laid out as tall as the row it sits in, but touched over 40dp: the
            // card's padding above and below takes the rest.
            .layout { measurable, constraints ->
                val placeable = measurable.measure(constraints.copy(minHeight = 0, maxHeight = Constraints.Infinity))
                val height = 22.dp.roundToPx()
                layout(placeable.width, height) { placeable.place(0, (height - placeable.height) / 2) }
            }
            .heightIn(min = 40.dp)
            .clip(RoundedCornerShape(20.dp))
            .then(
                if (onAssign != null) Modifier.clickable(role = Role.Button, onClickLabel = "Change assignee", onClick = onAssign) else Modifier,
            )
            .semantics(mergeDescendants = true) { contentDescription = said }
            .padding(start = 4.dp, end = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Avatar(person.name, person.colour, 22.dp, 10.sp)
        Text(label, color = colors.ink2, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1)
        if (onAssign != null) Icon(CylistIcons.ChevronDown, null, tint = colors.ink3, modifier = Modifier.size(12.dp).offset(x = (-3).dp))
    }
}

/**
 * Give a card to someone else: the project's members, you first and then by
 * how many open cards each has. There is no "Unassigned": a card always has an
 * assignee, and it must be a member, as on the web card's assignee select.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun AssignSheet(task: TaskSummary, state: BoardState, pick: (MemberRead) -> Unit, close: () -> Unit) {
    val colors = CylistTheme.colors
    var query by rememberSaveable { mutableStateOf("") }
    val open = openCardsOf(state.tasks, state.columns.lastOrNull()?.id)
    val me = state.me?.id
    val people = facesInOrder(state.members, { it.id }, { it.name }, open, me)
        .filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
    ModalBottomSheet(onDismissRequest = close, containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().imePadding().padding(bottom = 16.dp)) {
            Column(Modifier.padding(horizontal = 20.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text("Assign ${task.reference}", color = colors.ink, fontSize = 18.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
                Text(task.title, color = colors.ink3, fontSize = 14.sp, fontFamily = Figtree, maxLines = 2, overflow = TextOverflow.Ellipsis)
            }
            OutlinedTextField(
                value = query,
                onValueChange = { query = it },
                placeholder = { Text("Find a person", fontFamily = Figtree, color = colors.ink3) },
                singleLine = true,
                shape = RoundedCornerShape(8.dp),
                colors = OutlinedTextFieldDefaults.colors(focusedBorderColor = colors.ink, unfocusedBorderColor = colors.line2, cursorColor = colors.ink),
                modifier = Modifier.fillMaxWidth().padding(start = 20.dp, end = 20.dp, top = 14.dp, bottom = 6.dp),
            )
            LazyColumn(Modifier.fillMaxWidth()) {
                if (people.isEmpty()) {
                    item {
                        Text(
                            "Nobody on ${state.project?.key.orEmpty()} matches “${query.trim()}”.", color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree,
                            modifier = Modifier.padding(horizontal = 20.dp, vertical = 16.dp),
                        )
                    }
                }
                items(people, key = { it.id }) { person ->
                    val current = person.id == task.assignee.id
                    val count = openCardsText(open[person.id] ?: 0)
                    val name = if (person.id == me) "${person.name} (you)" else person.name
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 56.dp)
                            .clickable(role = Role.Button) {
                                if (!current) pick(person)
                                close()
                            }
                            .semantics(mergeDescendants = true) { contentDescription = "$name, $count" + if (current) ", assigned now" else "" }
                            .padding(horizontal = 20.dp, vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Avatar(person.name, person.colour, 32.dp, 12.sp)
                        Column(Modifier.weight(1f)) {
                            Text(name, color = colors.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(count, color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree)
                        }
                        if (current) Icon(CylistIcons.Tick, null, tint = colors.ok, modifier = Modifier.size(18.dp))
                    }
                }
            }
        }
    }
}

/**
 * The board's snackbar. It shows for [NOTICE_MS] and then goes, and its Undo,
 * when it has one, takes the reassignment back.
 */
@Composable
private fun NoticeBar(notice: BoardNotice?, actions: BoardActions, modifier: Modifier) {
    val colors = CylistTheme.colors
    val host = remember { SnackbarHostState() }
    LaunchedEffect(notice?.serial) {
        if (notice == null) return@LaunchedEffect
        val result = withTimeoutOrNull(NOTICE_MS) {
            host.showSnackbar(notice.text, actionLabel = if (notice.undo != null) "Undo" else null, duration = SnackbarDuration.Indefinite)
        }
        if (result == SnackbarResult.ActionPerformed) actions.undo(notice) else actions.dismissNotice(notice.serial)
    }
    SnackbarHost(host, modifier) { data ->
        Snackbar(
            data,
            shape = RoundedCornerShape(10.dp),
            containerColor = colors.ink,
            contentColor = colors.paper,
            actionColor = colors.paper,
        )
    }
}

@Composable
private fun Progress(task: TaskSummary) {
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
internal fun Avatar(
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

/** How long a column change takes, and how far the new list slides in. */
private const val SWITCH_MS = 200
private val SWITCH_SHIFT = 40.dp

/** How long the snackbar stays. */
private const val NOTICE_MS = 5_000L

/** The agent line's 2dp rule down its left edge. */
private fun Modifier.drawLeftRule(color: Color): Modifier = drawBehind {
    drawRect(color, size = Size(2.dp.toPx(), size.height))
}

/** "#4A7B8C" as a colour, or null when it is missing or not one. */
internal fun hexOrNull(value: String?): Color? =
    value?.removePrefix("#")?.takeIf { it.length == 6 }?.toLongOrNull(16)?.let { Color(0xFF000000 or it) }

internal fun hex(value: String?, fallback: Color): Color = hexOrNull(value) ?: fallback
