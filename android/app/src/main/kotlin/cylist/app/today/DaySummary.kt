package cylist.app.today

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cylist.api.generated.models.DayReport
import cylist.api.generated.models.TodoState
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import java.time.LocalDate

/**
 * The summary of a day: the web's "Day report", read from the same route, so
 * every line is worded by the server — the headline, what was finished, how
 * each to-do ended up, and what happened to each card.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SummarySheet(day: LocalDate, load: (LocalDate, (Result<DayReport>) -> Unit) -> Unit, close: () -> Unit) {
    val colors = CylistTheme.colors
    var result by remember(day) { mutableStateOf<Result<DayReport>?>(null) }
    var attempt by remember(day) { mutableStateOf(0) }
    LaunchedEffect(day, attempt) {
        result = null
        load(day) { result = it }
    }

    ModalBottomSheet(onDismissRequest = close, sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true), containerColor = colors.paper) {
        Column(Modifier.fillMaxWidth().fillMaxSize(0.88f)) {
            Row(Modifier.fillMaxWidth().padding(start = 18.dp, end = 8.dp, bottom = 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Summary", color = colors.ink, fontSize = 17.sp, fontWeight = FontWeight.Bold, fontFamily = Figtree)
                Text(dayLong(day), color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree, modifier = Modifier.weight(1f))
                SmallButton("Done", onClick = close)
            }
            val loaded = result
            when {
                loaded == null -> Text("Reading the day…", color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, modifier = Modifier.padding(18.dp))
                loaded.isFailure -> Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(loaded.exceptionOrNull()?.message ?: "Something went wrong.", color = colors.blocked, fontSize = 14.sp, fontFamily = Figtree)
                    SmallButton("Try again") { attempt++ }
                }
                else -> SummaryBody(loaded.getOrThrow())
            }
        }
    }
}

@Composable
private fun SummaryBody(report: DayReport) {
    val colors = CylistTheme.colors
    val todoCards = report.todos.orEmpty().filter { it.todos.isNotEmpty() }
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(start = 18.dp, end = 18.dp, bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        item { Text(report.headline, color = colors.ink, fontSize = 15.sp, lineHeight = 21.sp, fontFamily = Figtree) }

        if (todoCards.isNotEmpty()) {
            item { SectionTitle("To-dos") }
            todoCards.forEach { card ->
                item {
                    Column(Modifier.padding(bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        CardHead(card.reference, card.title, card.column ?: "Cold storage")
                        card.todos.forEach { todo ->
                            val mark = when (todo.state) {
                                TodoState.done -> "✓"
                                TodoState.moved -> "→"
                                else -> "○"
                            }
                            val fate = listOfNotNull(
                                if (todo.state == TodoState.moved) todo.movedTo?.let { "moved to ${dayShort(LocalDate.parse(it))}" } else null,
                                if (todo.state == TodoState.not_done) "not done" else null,
                                todo.carriedFrom?.let { "from ${weekdayShort(LocalDate.parse(it))}" },
                            ).joinToString(" · ")
                            Text(
                                buildAnnotatedString {
                                    append("$mark  ")
                                    withStyle(SpanStyle(color = if (todo.state == TodoState.done) colors.ink3 else colors.ink)) { append(todo.text) }
                                    if (fate.isNotEmpty()) withStyle(SpanStyle(color = colors.ink3, fontSize = 12.sp)) { append("  $fate") }
                                },
                                color = colors.ink2,
                                fontSize = 14.sp,
                                fontFamily = Figtree,
                                modifier = Modifier.padding(start = 6.dp),
                            )
                        }
                    }
                }
            }
        }

        val finished = report.tasks.filter { it.finished }
        if (finished.isNotEmpty()) {
            item { SectionTitle("Finished") }
            finished.forEach { item { CardHead(it.reference, it.title, null) } }
        }

        if (report.tasks.isNotEmpty()) {
            item { SectionTitle("Cards") }
            report.tasks.forEach { card ->
                item {
                    Column(Modifier.padding(bottom = 8.dp), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                        CardHead(card.reference, card.title, card.column ?: card.parent?.let { "of $it" } ?: "deleted")
                        card.propertyEntries.forEach { Text(it.summary, color = colors.ink2, fontSize = 13.sp, fontFamily = Figtree, modifier = Modifier.padding(start = 6.dp)) }
                    }
                }
            }
        }

        if (report.elsewhere.isNotEmpty()) {
            item { SectionTitle("Elsewhere on the project") }
            report.elsewhere.forEach { item { Text(it.summary, color = colors.ink2, fontSize = 13.sp, fontFamily = Figtree) } }
        }
    }
}

@Composable
private fun SectionTitle(text: String) {
    Text(text, color = CylistTheme.colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(top = 12.dp))
}

@Composable
private fun CardHead(reference: String?, title: String, where: String?) {
    val colors = CylistTheme.colors
    Text(
        buildAnnotatedString {
            if (reference != null) {
                withStyle(SpanStyle(fontFamily = PlexMono, fontSize = 12.sp, color = colors.ink3, fontWeight = FontWeight.Normal)) { append(reference) }
                append("  ")
            }
            append(title)
            if (where != null) withStyle(SpanStyle(color = colors.ink3, fontSize = 12.sp, fontWeight = FontWeight.Normal)) { append("  $where") }
        },
        color = colors.ink,
        fontSize = 14.5.sp,
        fontWeight = FontWeight.SemiBold,
        fontFamily = Figtree,
    )
}
