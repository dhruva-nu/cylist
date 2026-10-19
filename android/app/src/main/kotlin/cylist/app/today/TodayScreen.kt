package cylist.app.today

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.relocation.BringIntoViewRequester
import androidx.compose.foundation.relocation.bringIntoViewRequester
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.selection.toggleable
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DatePicker
import androidx.compose.material3.DatePickerDialog
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.SelectableDates
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.rememberDatePickerState
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextRange
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.TextFieldValue
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.api.generated.models.FiledItem
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.PickRead
import cylist.api.generated.models.TaskRead
import cylist.api.generated.models.TodoRead
import cylist.api.generated.models.TodoState
import cylist.api.generated.models.WrapUpResult
import cylist.app.board.initialsOf
import cylist.app.theme.CylistColors
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneOffset
import java.util.UUID

/*
 * The Today tab: the "Today" artboard of the Cylist Mobile design. One pane,
 * where the web planner has two: the day's plan with its to-dos, and the open
 * cards in a sheet ("Add cards") rather than beside it. Wrapping up is a sheet
 * too, ending on a result sheet, as the web's dialog ends on a result screen.
 */

/** The Today tab, driven by [viewModel]. */
@Composable
fun TodayScreen(viewModel: TodayViewModel, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsStateWithLifecycle()

    // As the Board does: the socket is held while the screen is started, and
    // its `ready` on coming back reads the plan again.
    // Opening Today always starts on today, as on the web.
    LaunchedEffect(viewModel) { viewModel.backToToday() }

    LifecycleStartEffect(viewModel) {
        viewModel.connect()
        onStopOrDispose { viewModel.disconnect() }
    }

    TodayContent(
        state = state,
        actions = TodayActions(
            showDay = viewModel::showDay,
            backToToday = viewModel::backToToday,
            pick = { task -> viewModel.pick(task) },
            unpick = { id -> viewModel.unpick(id) },
            addTodo = viewModel::addTodo,
            tick = viewModel::tick,
            deleteTodo = viewModel::deleteTodo,
            wrapUp = viewModel::wrapUp,
            retry = { viewModel.refresh() },
        ),
        modifier = modifier,
    )
}

class TodayActions(
    val showDay: (LocalDate) -> Unit = {},
    val backToToday: () -> Unit = {},
    val pick: (TaskRead) -> Unit = {},
    val unpick: (UUID) -> Unit = {},
    val addTodo: (UUID, String, (String?) -> Unit) -> Unit = { _, _, done -> done(null) },
    val tick: (UUID, Boolean) -> Unit = { _, _ -> },
    val deleteTodo: (UUID) -> Unit = {},
    val wrapUp: (LocalDate, LocalDate, List<UUID>, (Result<WrapUpResult>) -> Unit) -> Unit = { _, _, _, _ -> },
    val retry: () -> Unit = {},
)

private enum class Sheet { None, Cards, WrapUp }

/** The screen with nothing behind it but [state], so it can be drawn on its own. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun TodayContent(state: TodayState, actions: TodayActions, modifier: Modifier = Modifier) {
    val colors = CylistTheme.colors
    var sheet by rememberSaveable { mutableStateOf(Sheet.None) }
    var result by remember { mutableStateOf<WrapUpResult?>(null) }
    var choosingDay by remember { mutableStateOf(false) }
    var confirmUnpick by remember { mutableStateOf<PickRead?>(null) }

    val plan = state.plan
    val picks = plan?.picks.orEmpty()
    val total = picks.sumOf { it.todos.size }
    val done = plan?.doneCount ?: 0
    val onToday = state.day == state.today

    fun unpick(pick: PickRead) {
        if (pick.todos.isEmpty()) actions.unpick(pick.id) else confirmUnpick = pick
    }

    Column(modifier.fillMaxSize().background(colors.bg).statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 8.dp, top = 14.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("Today", color = colors.ink, fontSize = 22.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree, letterSpacing = (-0.33).sp)
            Spacer(Modifier.weight(1f))
            IconAction(CylistIcons.Plus, "Add cards to the day", enabled = state.projectKey != null) { sheet = Sheet.Cards }
        }
        Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconAction(CylistIcons.ChevronLeft, "The day before", size = 18.dp) { actions.showDay(state.day.minusDays(1)) }
            DayButton(dayShort(state.day), "Which day to plan: ${dayLong(state.day)}") { choosingDay = true }
            IconAction(CylistIcons.ChevronRight, "The day after", size = 18.dp) { actions.showDay(state.day.plusDays(1)) }
            Spacer(Modifier.weight(1f))
            SmallButton("Today", enabled = !onToday, ghost = true, onClick = actions.backToToday)
        }
        Row(
            Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 12.dp, bottom = 8.dp),
            verticalAlignment = Alignment.Bottom,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            Text(planTitle(state.day, state.today), color = colors.ink, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            planNote(picks)?.let { Text(it, color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree) }
        }
        Rule(colors)

        Box(Modifier.weight(1f)) {
            when {
                state.projectKey == null && !state.loading -> Message("Open a project on the Board to plan your day on it.", null)
                plan == null && state.error != null -> Message(state.error, actions.retry)
                plan == null -> Message("Reading the plan…", null)
                else -> PlanList(state, picks, onOpenCards = { sheet = Sheet.Cards }, actions = actions, unpick = ::unpick)
            }
        }

        // The progress and "Wrap up" step aside while a to-do is being typed.
        if (!WindowInsets.isImeVisible) Footer(done, total, enabled = (plan?.openCount ?: 0) > 0) { sheet = Sheet.WrapUp }
    }

    if (choosingDay) {
        DayPickerDialog(initial = state.day, earliest = null, close = { choosingDay = false }) { actions.showDay(it) }
    }
    confirmUnpick?.let { pick ->
        ConfirmUnpick(pick, close = { confirmUnpick = null }) { actions.unpick(pick.id) }
    }
    when (sheet) {
        Sheet.Cards -> CardsSheet(state, actions, unpick = ::unpick, close = { sheet = Sheet.None })
        Sheet.WrapUp -> WrapUpSheet(state, actions, close = { sheet = Sheet.None }) {
            sheet = Sheet.None
            result = it
        }
        Sheet.None -> Unit
    }
    result?.let { wrapped ->
        ResultSheet(
            wrapped,
            close = { result = null },
            goTo = {
                result = null
                actions.showDay(LocalDate.parse(wrapped.toDay))
            },
        )
    }
}

/** The done-of-total meter and "Wrap up the day". */
@Composable
private fun Footer(done: Int, total: Int, enabled: Boolean, wrapUp: () -> Unit) {
    val colors = CylistTheme.colors
    Rule(colors)
    Row(
        Modifier.fillMaxWidth().background(colors.bg).padding(horizontal = 16.dp, vertical = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(
                buildAnnotatedString {
                    withStyle(SpanStyle(color = colors.ink, fontWeight = FontWeight.SemiBold)) { append("$done of $total") }
                    append(" done")
                },
                color = colors.ink2,
                fontSize = 13.sp,
                fontFamily = Figtree,
            )
            Box(Modifier.fillMaxWidth().height(3.dp).clip(RoundedCornerShape(3.dp)).background(colors.line)) {
                if (total > 0 && done > 0) {
                    Box(Modifier.fillMaxWidth(done.toFloat() / total).height(3.dp).clip(RoundedCornerShape(3.dp)).background(colors.ok))
                }
            }
        }
        GoButton("Wrap up the day", enabled = enabled, onClick = wrapUp)
    }
}

@Composable
private fun PlanList(
    state: TodayState,
    picks: List<PickRead>,
    onOpenCards: () -> Unit,
    actions: TodayActions,
    unpick: (PickRead) -> Unit,
) {
    val colors = CylistTheme.colors
    val list = rememberLazyListState()
    val late = state.lateUnplanned
    LazyColumn(
        state = list,
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 20.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        if (state.error != null) {
            item(key = "error") { Text(state.error, color = colors.blocked, fontSize = 13.sp, fontFamily = Figtree) }
        }
        if (late != null) {
            item(key = "nudge") {
                Row(Modifier.fillMaxWidth().padding(2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        buildAnnotatedString {
                            withStyle(SpanStyle(color = colors.blocked, fontWeight = FontWeight.SemiBold)) { append(lateSaid(late, state.today)) }
                            append(" and isn’t on today’s plan.")
                        },
                        color = colors.ink2,
                        fontSize = 13.sp,
                        lineHeight = 18.sp,
                        fontFamily = Figtree,
                        modifier = Modifier.weight(1f),
                    )
                    SmallButton("Add it") { actions.pick(late) }
                }
            }
        }
        if (picks.isEmpty()) {
            item(key = "empty") {
                Text(
                    buildAnnotatedString {
                        append(if (state.day == state.today) "Pick a card from the board to plan the day around it." else "Nothing is planned for ${dayLong(state.day)}.")
                        append("\nIts to-dos go under it here: type ")
                        withStyle(SpanStyle(fontFamily = PlexMono, color = colors.ink)) { append("@") }
                        append(" to tag a person, ")
                        withStyle(SpanStyle(fontFamily = PlexMono, color = colors.ink)) { append(">") }
                        append(" to tag a file.")
                    },
                    color = colors.ink2,
                    fontSize = 14.sp,
                    lineHeight = 22.sp,
                    fontFamily = Figtree,
                    textAlign = TextAlign.Center,
                    modifier = Modifier.fillMaxWidth().padding(start = 12.dp, end = 12.dp, top = 36.dp, bottom = 8.dp),
                )
            }
        }
        items(picks, key = { it.id }) { pick ->
            PickBlock(pick, state, actions, unpick = { unpick(pick) })
        }
        item(key = "add-cards") {
            Row(
                Modifier
                    .fillMaxWidth()
                    .heightIn(min = 48.dp)
                    .clip(RoundedCornerShape(10.dp))
                    .border(1.dp, colors.line2, RoundedCornerShape(10.dp))
                    .clickable(role = Role.Button, onClick = onOpenCards),
                horizontalArrangement = Arrangement.Center,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(CylistIcons.Plus, null, tint = colors.ink2, modifier = Modifier.size(16.dp))
                Spacer(Modifier.width(8.dp))
                Text("Add cards from the board", color = colors.ink2, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            }
        }
    }
}

/** One card on the day: its name, its to-dos, and the box that adds another. */
@Composable
private fun PickBlock(pick: PickRead, state: TodayState, actions: TodayActions, unpick: () -> Unit) {
    val colors = CylistTheme.colors
    Column(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(colors.paper)
            .border(1.dp, colors.line, RoundedCornerShape(10.dp))
            .padding(start = 12.dp, end = 4.dp, top = 10.dp, bottom = 4.dp)
            .semantics { contentDescription = pick.task.reference },
    ) {
        Row(verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Column(Modifier.weight(1f).padding(vertical = 2.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                Text(
                    buildAnnotatedString {
                        withStyle(SpanStyle(fontFamily = PlexMono, fontSize = 12.sp, color = colors.ink3, fontWeight = FontWeight.Normal)) { append(pick.task.reference) }
                        append("  ")
                        append(pick.task.title)
                    },
                    color = colors.ink,
                    fontSize = 15.sp,
                    fontWeight = FontWeight.SemiBold,
                    lineHeight = 20.sp,
                    fontFamily = Figtree,
                )
                Text(pick.task.column ?: "Cold storage", color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
            }
            IconAction(CylistIcons.Close, "Take ${pick.task.reference} off the day", size = 16.dp, onClick = unpick)
        }
        pick.todos.forEach { todo ->
            TodoRow(
                todo = todo,
                people = state.people,
                files = state.files,
                error = state.todoErrors[todo.id],
                tick = { actions.tick(todo.id, it) },
                delete = { actions.deleteTodo(todo.id) },
            )
        }
        Composer(pick, state.people, state.files, actions.addTodo)
    }
}

@Composable
private fun TodoRow(
    todo: TodoRead,
    people: List<PersonRead>,
    files: List<FiledItem>,
    error: String?,
    tick: (Boolean) -> Unit,
    delete: () -> Unit,
) {
    val colors = CylistTheme.colors
    val done = todo.state == TodoState.done
    val moved = todo.state == TodoState.moved
    val faded = done || moved || todo.state == TodoState.not_done
    Column(Modifier.fillMaxWidth()) {
        Rule(colors)
        Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.Top) {
            TickBox(
                ticked = done,
                dashed = moved,
                enabled = !moved,
                label = if (done) "Mark not done" else "Mark done",
                modifier = Modifier.padding(end = 4.dp),
                onChange = tick,
            )
            Text(
                tagged(todo.text, people, files, colors, marks = todoMarks(todo), struck = done),
                color = if (faded) colors.ink3 else colors.ink,
                fontSize = 14.5.sp,
                lineHeight = 20.sp,
                fontFamily = Figtree,
                modifier = Modifier.weight(1f).padding(top = 10.dp, bottom = 8.dp),
            )
            if (!moved) IconAction(CylistIcons.Close, "Delete this to-do", size = 13.dp, width = 36.dp, tint = colors.ink3, onClick = delete)
        }
        if (error != null) {
            Text(error, color = colors.blocked, fontSize = 12.sp, fontFamily = Figtree, modifier = Modifier.padding(start = 32.dp, bottom = 6.dp))
        } else if (todo.state == TodoState.`open`) {
            onTheCard(todo)?.let { Text(it, color = colors.ink3, fontSize = 11.sp, fontFamily = Figtree, modifier = Modifier.padding(start = 32.dp, bottom = 6.dp)) }
        }
    }
}

/** A to-do's words with its tags drawn as tags, then its marks ("from Thu", "→ Mon 19 Oct"). */
private fun tagged(
    text: String,
    people: List<PersonRead>,
    files: List<FiledItem>,
    colors: CylistColors,
    marks: List<String> = emptyList(),
    struck: Boolean = false,
): AnnotatedString = buildAnnotatedString {
    val words = if (struck) SpanStyle(textDecoration = TextDecoration.LineThrough) else SpanStyle()
    withStyle(words) {
        splitTags(text, people, files).forEach { run ->
            when (run) {
                is TagRun.Person -> withStyle(SpanStyle(color = colors.brandInk, fontWeight = FontWeight.SemiBold)) { append(run.text) }
                // The design draws a file tag as its name in link colour, without the `>`.
                is TagRun.File -> withStyle(SpanStyle(color = colors.link, fontWeight = FontWeight.Medium)) { append(run.text.drop(1)) }
                is TagRun.Plain -> append(run.text)
            }
        }
    }
    marks.forEach { mark ->
        // Non-breaking inside, so "from Mon" wraps as one piece rather than leaving "Mon" alone on a line.
        withStyle(SpanStyle(color = colors.ink3, fontSize = 12.sp)) { append("   " + mark.replace(' ', '\u00A0')) }
    }
}

/** The box under a card's to-dos: `@` offers people, `>` offers files, Enter adds. */
@Composable
private fun Composer(pick: PickRead, people: List<PersonRead>, files: List<FiledItem>, addTodo: (UUID, String, (String?) -> Unit) -> Unit) {
    val colors = CylistTheme.colors
    var draft by remember(pick.id) { mutableStateOf(TextFieldValue("")) }
    var busy by remember(pick.id) { mutableStateOf(false) }
    var error by remember(pick.id) { mutableStateOf<String?>(null) }

    fun submit() {
        if (draft.text.isBlank() || busy) return
        busy = true
        error = null
        addTodo(pick.id, draft.text) { failed ->
            busy = false
            if (failed == null) draft = TextFieldValue("") else error = failed
        }
    }

    val tag = if (draft.selection.collapsed) tagQuery(draft.text, draft.selection.start) else null
    val suggestions: List<Suggestion> = when (tag?.sigil) {
        PERSON_SIGIL -> matchingPeople(tag.query, people).take(SUGGESTIONS).map { Suggestion.Person(it) }
        FILE_SIGIL -> matchingFiles(tag.query, files).take(SUGGESTIONS).map { Suggestion.File(it) }
        else -> emptyList()
    }

    Rule(colors)
    Row(Modifier.fillMaxWidth().padding(end = 8.dp, top = 2.dp, bottom = 2.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        BasicTextField(
            value = draft,
            onValueChange = { draft = if (it.text.length <= TODO_MAX) it else draft },
            singleLine = true,
            textStyle = TextStyle(color = colors.ink, fontSize = 14.5.sp, fontFamily = Figtree),
            cursorBrush = SolidColor(colors.ink),
            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { submit() }),
            modifier = Modifier.weight(1f).heightIn(min = 42.dp).semantics { contentDescription = "Add a to-do to ${pick.task.reference}" },
            decorationBox = { field ->
                Box(Modifier.heightIn(min = 42.dp), contentAlignment = Alignment.CenterStart) {
                    if (draft.text.isEmpty()) Text("Add a to-do — @ person, > file", color = colors.ink3, fontSize = 14.5.sp, fontFamily = Figtree)
                    field()
                }
            },
        )
        if (draft.text.isNotBlank()) SmallButton(if (busy) "Adding…" else "Add", enabled = !busy) { submit() }
    }
    error?.let { Text(it, color = colors.blocked, fontSize = 12.sp, fontFamily = Figtree, modifier = Modifier.padding(bottom = 6.dp)) }
    val offered = remember(pick.id) { BringIntoViewRequester() }
    val offering = tag != null && suggestions.isNotEmpty()
    // The list drops out below the box; scroll so it is seen, not left under the fold.
    LaunchedEffect(offering, suggestions.size) { if (offering) offered.bringIntoView() }
    if (tag != null && suggestions.isNotEmpty()) {
        Column(
            Modifier
                .bringIntoViewRequester(offered)
                .fillMaxWidth()
                .padding(end = 8.dp, bottom = 8.dp)
                .clip(RoundedCornerShape(10.dp))
                .background(colors.paper)
                .border(1.dp, colors.line2, RoundedCornerShape(10.dp))
                .padding(4.dp),
        ) {
            suggestions.forEachIndexed { index, suggestion ->
                Row(
                    Modifier
                        .fillMaxWidth()
                        .heightIn(min = 44.dp)
                        .clip(RoundedCornerShape(6.dp))
                        .background(if (index == 0) colors.brandSoft else Color.Transparent)
                        .clickable(role = Role.Button) {
                            val done = applyTag(draft.text, tag, suggestion.name)
                            draft = TextFieldValue(done.value, TextRange(done.caret))
                        }
                        .padding(horizontal = 8.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    when (suggestion) {
                        is Suggestion.Person -> Avatar(suggestion.person)
                        is Suggestion.File -> Box(Modifier.width(24.dp), contentAlignment = Alignment.Center) {
                            Icon(CylistIcons.File, null, tint = colors.ink3, modifier = Modifier.size(16.dp))
                        }
                    }
                    Text(suggestion.name, color = colors.ink, fontSize = 14.sp, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                    Text(suggestion.note, color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis)
                }
            }
        }
    }
}

private sealed interface Suggestion {
    val name: String
    val note: String

    data class Person(val person: PersonRead) : Suggestion {
        override val name: String get() = person.name
        override val note: String get() = person.title
    }

    data class File(val file: FiledItem) : Suggestion {
        override val name: String get() = file.name
        override val note: String get() = file.folderPath
    }
}

@Composable
private fun Avatar(person: PersonRead) {
    val colour = person.colour.removePrefix("#").takeIf { it.length == 6 }?.toLongOrNull(16)?.let { Color(0xFF000000 or it) } ?: CylistTheme.colors.ink3
    Box(Modifier.size(24.dp).clip(CircleShape).background(colour), contentAlignment = Alignment.Center) {
        Text(initialsOf(person.name), color = Color.White, fontSize = 10.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
    }
}

/** The add-cards sheet: every open card on the board, to put on the day or take off it. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun CardsSheet(state: TodayState, actions: TodayActions, unpick: (PickRead) -> Unit, close: () -> Unit) {
    val colors = CylistTheme.colors
    var query by rememberSaveable { mutableStateOf("") }
    var mine by rememberSaveable { mutableStateOf(false) }
    val me = state.me?.id
    val groups = openCardGroups(state.tasks, state.columns, me, mine, query, state.today)
    val allCount = cardCount(openCardGroups(state.tasks, state.columns, me, false, "", state.today))
    val mineCount = cardCount(openCardGroups(state.tasks, state.columns, me, true, "", state.today))
    val pickedByTask = state.plan?.picks.orEmpty().associateBy { it.task.id }
    val columnName = state.columns.associate { it.id to it.name }

    ModalBottomSheet(onDismissRequest = close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().fillMaxSize(0.88f)) {
            Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Add cards", color = colors.ink, fontSize = 17.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
                Text("not in the last column", color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree, modifier = Modifier.weight(1f))
                SmallButton("Done", onClick = close)
            }
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, bottom = 10.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                BasicTextField(
                    value = query,
                    onValueChange = { query = it },
                    singleLine = true,
                    textStyle = TextStyle(color = colors.ink, fontSize = 15.sp, fontFamily = Figtree),
                    cursorBrush = SolidColor(colors.ink),
                    modifier = Modifier
                        .weight(1f)
                        .height(40.dp)
                        .border(1.dp, colors.line2, RoundedCornerShape(8.dp))
                        .semantics { contentDescription = "Find a card" },
                    decorationBox = { field ->
                        Box(Modifier.padding(horizontal = 12.dp), contentAlignment = Alignment.CenterStart) {
                            if (query.isEmpty()) Text("Find a card", color = colors.ink3, fontSize = 15.sp, fontFamily = Figtree)
                            field()
                        }
                    },
                )
                Segmented(
                    listOf("All $allCount" to !mine, "Mine $mineCount" to mine),
                    description = "Whose cards",
                ) { mine = it == 1 }
            }
            LazyColumn(Modifier.fillMaxWidth().weight(1f), contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 16.dp)) {
                if (groups.isEmpty()) {
                    item { Text(if (query.isBlank()) "No open cards." else "No open card matches that.", color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, modifier = Modifier.padding(16.dp)) }
                }
                groups.forEach { group ->
                    item(key = "group-${group.name}") {
                        Column(Modifier.padding(top = 8.dp)) {
                            Row(Modifier.fillMaxWidth().padding(start = 6.dp, end = 6.dp, top = 6.dp, bottom = 5.dp)) {
                                val tone = if (group.late) colors.blocked else colors.ink3
                                Text(group.name, color = tone, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.weight(1f))
                                Text("${group.tasks.size}", color = tone, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                            }
                            Box(Modifier.padding(horizontal = 6.dp)) { Rule(colors) }
                        }
                    }
                    items(group.tasks, key = { "${group.name}-${it.id}" }) { task ->
                        val picked = pickedByTask[task.id]
                        CardRow(task, columnName[task.columnId].orEmpty(), picked != null, state.today) {
                            if (picked == null) actions.pick(task) else unpick(picked)
                        }
                    }
                }
            }
        }
    }
}

/** One card to pick: a press puts it on the day, another takes it off (`CardRow`). */
@Composable
private fun CardRow(task: TaskRead, columnName: String, picked: Boolean, today: LocalDate, toggle: () -> Unit) {
    val colors = CylistTheme.colors
    val due = pickDueOf(task, today)
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 56.dp)
            .clip(RoundedCornerShape(8.dp))
            .toggleable(value = picked, role = Role.Checkbox, onValueChange = { toggle() })
            .semantics { contentDescription = if (picked) "Take ${task.reference} off the day" else "Put ${task.reference} on the day" }
            .padding(horizontal = 6.dp, vertical = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
            Text(
                buildAnnotatedString {
                    withStyle(SpanStyle(fontFamily = PlexMono, fontSize = 12.sp, color = colors.ink3, fontWeight = FontWeight.Normal)) { append(task.reference) }
                    append("  ")
                    withStyle(SpanStyle(color = if (picked) colors.ink2 else colors.ink)) { append(task.title) }
                },
                fontSize = 14.5.sp,
                lineHeight = 19.sp,
                fontWeight = FontWeight.SemiBold,
                fontFamily = Figtree,
            )
            Text(
                buildAnnotatedString {
                    append(columnName)
                    if (due != null) {
                        append(" · ")
                        val style = when (due.tone) {
                            DueTone.Late -> SpanStyle(color = colors.blocked, fontWeight = FontWeight.SemiBold)
                            DueTone.Today -> SpanStyle(color = colors.hold, fontWeight = FontWeight.SemiBold)
                            DueTone.Plain -> SpanStyle()
                        }
                        withStyle(style) { append(due.text) }
                    }
                    append(" · ")
                    append(task.assignee.name)
                },
                color = colors.ink3,
                fontSize = 12.sp,
                fontFamily = Figtree,
            )
        }
        Box(
            Modifier
                .size(28.dp)
                .then(if (picked) Modifier else Modifier.border(1.dp, colors.line2, RoundedCornerShape(6.dp))),
            contentAlignment = Alignment.Center,
        ) {
            Icon(if (picked) CylistIcons.Tick else CylistIcons.Plus, null, tint = if (picked) colors.ok else colors.ink3, modifier = Modifier.size(14.dp))
        }
    }
}

/** Closing the day on screen: ticked to-dos move on, the rest stay as not done (`WrapUpDialog`). */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun WrapUpSheet(state: TodayState, actions: TodayActions, close: () -> Unit, wrapped: (WrapUpResult) -> Unit) {
    val colors = CylistTheme.colors
    val day = state.day
    val open = openByCard(state.plan?.picks.orEmpty())
    var target by rememberSaveable { mutableStateOf(WrapTarget.NextWorkingDay) }
    var otherDay by rememberSaveable { mutableStateOf(firstOtherDay(day).toString()) }
    var kept by remember { mutableStateOf(emptySet<UUID>()) }
    var choosing by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    val plan = WrapUpPlan(
        day = day,
        toDay = wrapTargetDay(day, target, LocalDate.parse(otherDay)),
        open = open.flatMap { (_, todos) -> todos.map { it.id } },
        kept = kept,
    )
    val dayName = weekdayName(day)

    ModalBottomSheet(onDismissRequest = close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().fillMaxSize(0.88f)) {
            Text("Wrap up $dayName", color = colors.ink, fontSize = 17.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree, modifier = Modifier.padding(start = 18.dp, end = 18.dp, bottom = 8.dp))
            LazyColumn(Modifier.fillMaxWidth().weight(1f), contentPadding = PaddingValues(start = 12.dp, end = 12.dp, bottom = 16.dp)) {
                if (plan.open.isEmpty()) {
                    item { Text("Nothing is open on $dayName. There is nothing to move on.", color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, modifier = Modifier.padding(6.dp)) }
                } else {
                    item {
                        Column(Modifier.padding(horizontal = 6.dp)) {
                            Text(plan.lead, color = colors.ink2, fontSize = 14.sp, lineHeight = 21.sp, fontFamily = Figtree, modifier = Modifier.padding(bottom = 12.dp))
                            Text("Move them to", color = colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(bottom = 6.dp))
                            Segmented(
                                listOf(
                                    "Next working day · ${dayShort(nextWorkingDay(day)).replace(' ', '\u00A0')}" to (target == WrapTarget.NextWorkingDay),
                                    "Another day" to (target == WrapTarget.AnotherDay),
                                ),
                                description = "Which day to move them to",
                                fill = true,
                            ) { target = if (it == 0) WrapTarget.NextWorkingDay else WrapTarget.AnotherDay }
                            if (target == WrapTarget.AnotherDay) {
                                Box(Modifier.padding(top = 8.dp)) {
                                    DayButton(dayShort(plan.toDay), "The day to move them to: ${dayLong(plan.toDay)}") { choosing = true }
                                }
                            }
                            Spacer(Modifier.height(14.dp))
                            Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.CenterVertically) {
                                TickBox(
                                    ticked = plan.allTicked,
                                    partial = !plan.allTicked && plan.moving.isNotEmpty(),
                                    label = "All open to-dos",
                                    modifier = Modifier.padding(end = 2.dp),
                                ) { kept = if (plan.allTicked) plan.open.toSet() else emptySet() }
                                Text("All open to-dos", color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.weight(1f))
                                Text("${plan.moving.size} of ${plan.open.size} moving", color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree)
                            }
                            Rule(colors)
                        }
                    }
                    items(open, key = { it.first.id }) { (pick, todos) ->
                        Column(Modifier.padding(start = 6.dp, end = 6.dp, top = 12.dp)) {
                            Text(
                                buildAnnotatedString {
                                    withStyle(SpanStyle(fontFamily = PlexMono, fontSize = 12.sp, color = colors.ink3, fontWeight = FontWeight.Normal)) { append(pick.task.reference) }
                                    append(" ")
                                    append(pick.task.title)
                                },
                                color = colors.ink,
                                fontSize = 13.5.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = Figtree,
                                modifier = Modifier.padding(bottom = 2.dp),
                            )
                            todos.forEach { todo ->
                                val ticked = todo.id !in kept
                                Rule(colors)
                                Row(Modifier.fillMaxWidth().heightIn(min = 44.dp), verticalAlignment = Alignment.Top) {
                                    TickBox(
                                        ticked = ticked,
                                        label = if (ticked) "Leave this one behind" else "Move this one on",
                                        modifier = Modifier.padding(end = 4.dp),
                                    ) { kept = if (ticked) kept + todo.id else kept - todo.id }
                                    Text(
                                        tagged(todo.text, state.people, state.files, colors, marks = listOf(if (ticked) plan.movingMark else "stays")),
                                        color = if (ticked) colors.ink else colors.ink3,
                                        fontSize = 14.5.sp,
                                        lineHeight = 20.sp,
                                        fontFamily = Figtree,
                                        modifier = Modifier.weight(1f).padding(top = 10.dp, bottom = 8.dp),
                                    )
                                }
                            }
                        }
                    }
                }
            }
            error?.let { Text(it, color = colors.blocked, fontSize = 13.sp, fontFamily = Figtree, modifier = Modifier.padding(horizontal = 18.dp, vertical = 4.dp)) }
            Rule(colors)
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 22.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                SmallButton("Cancel", large = true, onClick = close)
                GoButton(if (busy) "Wrapping up…" else plan.confirm, enabled = !busy && plan.open.isNotEmpty() && plan.canConfirm) {
                    busy = true
                    error = null
                    actions.wrapUp(plan.day, plan.toDay, plan.moving) { result ->
                        busy = false
                        result.fold(onSuccess = wrapped, onFailure = { error = it.message })
                    }
                }
            }
        }
    }

    if (choosing) {
        DayPickerDialog(initial = plan.toDay, earliest = day.plusDays(1), close = { choosing = false }) { otherDay = it.toString() }
    }
}

/** "Friday is wrapped up", once the server has answered. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ResultSheet(result: WrapUpResult, close: () -> Unit, goTo: () -> Unit) {
    val colors = CylistTheme.colors
    val day = LocalDate.parse(result.day)
    val toDay = LocalDate.parse(result.toDay)
    ModalBottomSheet(onDismissRequest = close, containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth()) {
            Text("${weekdayName(day)} is wrapped up", color = colors.ink, fontSize = 17.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree, modifier = Modifier.padding(horizontal = 18.dp))
            Text(
                wrapResultText(day, toDay, result.moved, result.notDone),
                color = colors.ink2,
                fontSize = 15.sp,
                lineHeight = 23.sp,
                fontFamily = Figtree,
                modifier = Modifier.padding(start = 18.dp, end = 18.dp, top = 6.dp),
            )
            Row(
                Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 22.dp, bottom = 22.dp),
                horizontalArrangement = Arrangement.spacedBy(8.dp, Alignment.End),
            ) {
                if (result.moved > 0) SmallButton("Go to ${weekdayName(toDay)}", large = true, onClick = goTo)
                GoButton("Done", onClick = close)
            }
        }
    }
}

@Composable
private fun ConfirmUnpick(pick: PickRead, close: () -> Unit, confirm: () -> Unit) {
    val colors = CylistTheme.colors
    AlertDialog(
        onDismissRequest = close,
        containerColor = colors.paper,
        text = { Text("Take ${pick.task.reference} off the day? Its to-dos for the day go with it.", color = colors.ink, fontSize = 15.sp, fontFamily = Figtree) },
        confirmButton = {
            TextButton(onClick = {
                close()
                confirm()
            }) { Text("Take it off", color = colors.blocked, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
        },
        dismissButton = { TextButton(onClick = close) { Text("Keep it", color = colors.ink2, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) } },
    )
}

/** The system date picker, in whole days; [earliest] keeps a wrap-up from moving to-dos backwards. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DayPickerDialog(initial: LocalDate, earliest: LocalDate?, close: () -> Unit, chosen: (LocalDate) -> Unit) {
    val colors = CylistTheme.colors
    val picker = rememberDatePickerState(
        initialSelectedDateMillis = initial.atStartOfDay(ZoneOffset.UTC).toInstant().toEpochMilli(),
        selectableDates = object : SelectableDates {
            override fun isSelectableDate(utcTimeMillis: Long): Boolean =
                earliest == null || !Instant.ofEpochMilli(utcTimeMillis).atZone(ZoneOffset.UTC).toLocalDate().isBefore(earliest)
        },
    )
    DatePickerDialog(
        onDismissRequest = close,
        confirmButton = {
            TextButton(onClick = {
                picker.selectedDateMillis?.let { chosen(Instant.ofEpochMilli(it).atZone(ZoneOffset.UTC).toLocalDate()) }
                close()
            }) { Text("Choose", color = colors.go, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
        },
        dismissButton = { TextButton(onClick = close) { Text("Cancel", color = colors.ink2, fontFamily = Figtree) } },
    ) {
        DatePicker(state = picker)
    }
}

// --- Pieces ---------------------------------------------------------------

/** The design's tick box: 18dp, green when ticked, dashed once the to-do has moved. */
@Composable
private fun TickBox(
    ticked: Boolean,
    label: String,
    modifier: Modifier = Modifier,
    dashed: Boolean = false,
    partial: Boolean = false,
    enabled: Boolean = true,
    onChange: (Boolean) -> Unit,
) {
    val colors = CylistTheme.colors
    Box(
        modifier
            .width(36.dp)
            .height(40.dp)
            .toggleable(value = ticked, enabled = enabled, role = Role.Checkbox, onValueChange = onChange)
            .semantics {
                contentDescription = label
                if (partial) stateDescription = "Some ticked"
            },
        contentAlignment = Alignment.Center,
    ) {
        val fill = ticked || partial
        Box(
            Modifier
                .size(18.dp)
                .drawBehind {
                    val corner = CornerRadius(4.dp.toPx())
                    if (ticked) {
                        drawRoundRect(colors.go, cornerRadius = corner)
                    } else {
                        drawRoundRect(
                            if (partial) colors.go else colors.line2,
                            cornerRadius = corner,
                            style = Stroke(
                                width = 1.5.dp.toPx(),
                                pathEffect = if (dashed) PathEffect.dashPathEffect(floatArrayOf(3.dp.toPx(), 2.dp.toPx())) else null,
                            ),
                        )
                    }
                },
            contentAlignment = Alignment.Center,
        ) {
            if (ticked) Icon(CylistIcons.Tick, null, tint = colors.goInk, modifier = Modifier.size(11.dp))
            if (!ticked && fill) Box(Modifier.size(width = 8.dp, height = 2.dp).background(colors.go))
        }
    }
}

@Composable
private fun IconAction(
    icon: ImageVector,
    description: String,
    size: Dp = 20.dp,
    width: Dp = 44.dp,
    enabled: Boolean = true,
    tint: Color = CylistTheme.colors.ink2,
    onClick: () -> Unit,
) {
    Box(
        Modifier
            .size(width = width, height = 44.dp)
            .clip(RoundedCornerShape(8.dp))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .semantics { contentDescription = description },
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = if (enabled) tint else CylistTheme.colors.line2, modifier = Modifier.size(size))
    }
}

/** The day label between ‹ and ›, which opens the date picker. */
@Composable
private fun DayButton(text: String, description: String, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Row(
        Modifier
            .heightIn(min = 40.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(colors.paper)
            .border(1.dp, colors.line2, RoundedCornerShape(8.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .semantics(mergeDescendants = true) { contentDescription = description }
            .padding(horizontal = 10.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(CylistIcons.Calendar, null, tint = colors.ink3, modifier = Modifier.size(15.dp))
        Text(text, color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
    }
}

/** `.btn` / `.btn.small` / `.btn.ghost`. */
@Composable
private fun SmallButton(text: String, enabled: Boolean = true, ghost: Boolean = false, large: Boolean = false, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    val shape = RoundedCornerShape(8.dp)
    Box(
        Modifier
            .heightIn(min = if (large) 40.dp else 34.dp)
            .clip(shape)
            .then(if (ghost) Modifier else Modifier.background(colors.paper).border(1.dp, colors.line2, shape))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = if (large) 14.dp else 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text,
            color = (if (ghost) colors.ink2 else colors.ink).copy(alpha = if (enabled) 1f else 0.45f),
            fontSize = if (large) 14.sp else 13.sp,
            fontWeight = FontWeight.SemiBold,
            fontFamily = Figtree,
            maxLines = 1,
        )
    }
}

/** `.btn.go`: the green button that confirms. */
@Composable
private fun GoButton(text: String, enabled: Boolean = true, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Box(
        Modifier
            .heightIn(min = 40.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(colors.go.copy(alpha = if (enabled) 1f else 0.45f))
            .clickable(enabled = enabled, role = Role.Button, onClick = onClick)
            .padding(horizontal = 14.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(text, color = colors.goInk, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1)
    }
}

/** `.segmented`: one choice of a few, the chosen one shaded. */
@Composable
private fun Segmented(options: List<Pair<String, Boolean>>, description: String, fill: Boolean = false, choose: (Int) -> Unit) {
    val colors = CylistTheme.colors
    val shape = RoundedCornerShape(8.dp)
    Row(
        Modifier
            .then(if (fill) Modifier.fillMaxWidth() else Modifier)
            .clip(shape)
            .border(1.dp, colors.line2, shape)
            .height(IntrinsicSize.Min)
            .semantics { contentDescription = description },
    ) {
        options.forEachIndexed { index, (label, on) ->
            if (index > 0) Box(Modifier.width(1.dp).heightIn(min = 38.dp).fillMaxHeight().background(colors.line2))
            Box(
                Modifier
                    .then(if (fill) Modifier.weight(1f) else Modifier)
                    .heightIn(min = 38.dp)
                    .fillMaxHeight()
                    .background(if (on) colors.paper2 else Color.Transparent)
                    .toggleable(value = on, role = Role.RadioButton, onValueChange = { choose(index) })
                    .padding(horizontal = 11.dp),
                contentAlignment = Alignment.Center,
            ) {
                // Two lines rather than an ellipsis, so "Next working day · Tue 20 Oct" keeps its date.
                Text(
                    label,
                    color = if (on) colors.ink else colors.ink2,
                    fontSize = 13.sp,
                    lineHeight = 16.sp,
                    fontWeight = FontWeight.SemiBold,
                    fontFamily = Figtree,
                    textAlign = TextAlign.Center,
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.padding(vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun Rule(colors: CylistColors) {
    Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
}

@Composable
private fun Message(text: String, retry: (() -> Unit)?) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(text, color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, textAlign = TextAlign.Center)
        if (retry != null) TextButton(onClick = retry) { Text("Try again", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
    }
}

/** How many people or files one sigil offers at once on the phone, as the design shows. */
private const val SUGGESTIONS = 4

/** The server's limit on a to-do's words (`TEXT_MAX_LENGTH`). */
private const val TODO_MAX = 500
