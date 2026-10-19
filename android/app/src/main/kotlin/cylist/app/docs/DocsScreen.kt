package cylist.app.docs

import android.content.Intent
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.semantics.stateDescription
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.api.generated.models.DocAnswer
import cylist.api.generated.models.DocListing
import cylist.api.generated.models.DocTopicWithDocs
import cylist.api.generated.models.DocTree
import cylist.api.generated.models.PersonRead
import cylist.api.generated.models.SectionFound
import cylist.app.board.initialsOf
import cylist.app.docs.markdown.MarkdownBlock
import cylist.app.docs.markdown.MarkdownColumn
import cylist.app.docs.markdown.MarkdownContext
import cylist.app.docs.markdown.MarkdownDoc
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons
import cylist.app.ui.icon
import kotlinx.coroutines.launch
import java.util.UUID

/*
 * The Docs tab: the mobile design's three Docs artboards (the "Cylist Mobile"
 * canvas: Docs, Docs — reading a doc, Docs — an answer) on the web Docs page's
 * data and wording (frontend/src/routes/docs/). Read-only: writing and filing
 * docs stays on the web, where the editor is.
 */

private object DocsIcons {
    val Ask = icon("ask", 24f, 2f, "M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3", "M12 17h.01", "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18z")
    val ChevronRight = icon("chevron-right", 24f, 2.2f, "m9 18 6-6-6-6")
    val Share = icon(
        "share-link", 24f, 2f,
        "M10 14a4.5 4.5 0 0 0 6.4 0l3-3a4.5 4.5 0 0 0-6.4-6.4l-1 1",
        "M14 10a4.5 4.5 0 0 0-6.4 0l-3 3a4.5 4.5 0 0 0 6.4 6.4l1-1",
    )
}

/** The Docs tab for [projectKey], the project the board is on, on [server]. */
@Composable
fun DocsScreen(viewModel: DocsViewModel, projectKey: String?, server: String, modifier: Modifier = Modifier) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    LaunchedEffect(projectKey) { viewModel.showProject(projectKey) }
    // Each time the tab comes on, the tree (and the open doc) are fetched again,
    // as the web refetches them when its window regains focus.
    LifecycleStartEffect(viewModel) {
        viewModel.refresh()
        onStopOrDispose { }
    }
    BackHandler(enabled = state.stack.size > 1) { viewModel.back() }

    val context = LocalContext.current
    val share = { url: String ->
        val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, url)
        context.startActivity(Intent.createChooser(send, null))
    }

    Box(modifier.fillMaxSize().background(CylistTheme.colors.bg).statusBarsPadding()) {
        val key = state.projectKey
        when (val page = state.page) {
            DocsPage.Tree -> TreePage(state, viewModel)
            is DocsPage.Reading -> ReaderPage(
                state = state,
                page = page,
                pageUrl = key?.let { docPageUrl(server, it, page.docId) } ?: server,
                back = { viewModel.back() },
                share = { key?.let { share(docPageUrl(server, it, page.docId)) } },
                retry = { viewModel.refresh() },
            )
            is DocsPage.Answer -> AnswerPage(
                state = state,
                question = page.question,
                pageUrl = key?.let { answerPageUrl(server, it, page.question) } ?: server,
                back = { viewModel.toTree() },
                share = { key?.let { share(answerPageUrl(server, it, page.question)) } },
                open = viewModel::openFromAnswer,
                retry = viewModel::retryAsk,
            )
        }
    }
}

// The tree --------------------------------------------------------------------

@Composable
private fun TreePage(state: DocsState, viewModel: DocsViewModel) {
    val colors = CylistTheme.colors
    val tree = state.tree
    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(start = 18.dp, end = 8.dp, top = 14.dp, bottom = 2.dp).heightIn(min = 44.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Docs", modifier = Modifier.semantics { heading() }, style = titleStyle(22.sp))
            if (tree != null) {
                val count = "${tree.docCount} doc${if (tree.docCount == 1) "" else "s"}"
                Text(
                    listOfNotNull(state.projectKey, count).joinToString(" · "),
                    modifier = Modifier.padding(start = 8.dp),
                    color = colors.ink3,
                    fontSize = 13.sp,
                    fontFamily = Figtree,
                )
            }
        }
        // As on the web, there is nothing to ask until there is a doc.
        if (tree != null && tree.docCount > 0) {
            AskBox(state.draft, viewModel::setDraft, viewModel::ask)
        }
        when {
            state.projectKey == null -> Message("No project to show docs for yet.")
            tree == null && state.treeError != null -> Message(state.treeError, retry = viewModel::refresh)
            tree == null -> Message("Loading docs…")
            else -> Tree(tree, state.expanded, viewModel::toggleTopic, viewModel::openFromTree)
        }
    }
}

@Composable
private fun AskBox(draft: String, setDraft: (String) -> Unit, ask: () -> Unit) {
    val colors = CylistTheme.colors
    val focus = LocalFocusManager.current
    val submit = {
        focus.clearFocus()
        ask()
    }
    Row(
        Modifier
            .fillMaxWidth()
            .padding(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 12.dp)
            .height(44.dp)
            .clip(RoundedCornerShape(8.dp))
            .background(colors.paper)
            .border(BorderStroke(1.dp, colors.line2), RoundedCornerShape(8.dp))
            .padding(start = 12.dp, end = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Icon(DocsIcons.Ask, null, tint = colors.ink3, modifier = Modifier.size(17.dp))
        Box(Modifier.weight(1f), contentAlignment = Alignment.CenterStart) {
            if (draft.isEmpty()) Text("Ask the docs a question", color = colors.ink3, fontSize = 15.sp, fontFamily = Figtree)
            BasicTextField(
                value = draft,
                onValueChange = setDraft,
                singleLine = true,
                textStyle = TextStyle(color = colors.ink, fontSize = 15.sp, fontFamily = Figtree),
                cursorBrush = SolidColor(colors.ink),
                keyboardOptions = KeyboardOptions(imeAction = ImeAction.Search),
                keyboardActions = KeyboardActions(onSearch = { submit() }),
                modifier = Modifier.fillMaxWidth().semantics { contentDescription = "Ask the docs" },
            )
        }
        if (draft.isNotBlank()) {
            Box(
                Modifier
                    .heightIn(min = 34.dp)
                    .clip(RoundedCornerShape(6.dp))
                    .background(colors.go)
                    .clickable(role = Role.Button, onClick = submit)
                    .padding(horizontal = 10.dp),
                contentAlignment = Alignment.Center,
            ) {
                Text("Ask", color = colors.goInk, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            }
        }
    }
}

@Composable
private fun Tree(tree: DocTree, expanded: Set<UUID>, toggle: (UUID) -> Unit, open: (UUID) -> Unit) {
    val colors = CylistTheme.colors
    if (tree.sections.sumOf { it.topics.size } == 0) {
        Message(
            "No topics yet. Add one under Product or Engineering on the web — “Goals”, “APIs”, " +
                "“DB schema” — and docs are filed under it.",
        )
        return
    }
    LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
        tree.sections.forEach { section ->
            item(key = "section-${section.section}") {
                Row(
                    Modifier
                        .fillMaxWidth()
                        .padding(top = 14.dp)
                        .padding(horizontal = 2.dp)
                        .semantics(mergeDescendants = true) { heading() },
                    verticalAlignment = Alignment.Bottom,
                    horizontalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(section.label, color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                    Text("${section.topics.sumOf { it.docs.size }}", color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
                }
                Rule(Modifier.padding(top = 6.dp))
            }
            section.topics.forEach { topic ->
                val open = topic.id in expanded
                item(key = "topic-${topic.id}") { TopicRow(topic, open) { toggle(topic.id) } }
                if (open) {
                    if (topic.docs.isEmpty()) {
                        item(key = "empty-${topic.id}") {
                            Text(
                                "No docs in this topic yet.",
                                modifier = Modifier.padding(start = 32.dp, top = 10.dp, bottom = 10.dp),
                                color = colors.ink3,
                                fontSize = 13.sp,
                                fontFamily = Figtree,
                            )
                        }
                    }
                    itemsIndexed(topic.docs, key = { _, doc -> "doc-${doc.id}" }) { index, doc ->
                        DocRow(doc, last = index == topic.docs.lastIndex) { open(doc.id) }
                    }
                }
            }
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun TopicRow(topic: DocTopicWithDocs, open: Boolean, toggle: () -> Unit) {
    val colors = CylistTheme.colors
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .clickable(role = Role.Button, onClick = toggle)
                .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }
                .padding(horizontal = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Icon(DocsIcons.ChevronRight, null, tint = colors.ink3, modifier = Modifier.size(14.dp).rotate(if (open) 90f else 0f))
            Text(
                topic.name,
                modifier = Modifier.weight(1f),
                color = colors.ink,
                fontSize = 15.sp,
                fontWeight = FontWeight.Medium,
                fontFamily = Figtree,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
            )
            Text("${topic.docs.size}", color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
        }
        if (!open) Rule()
    }
}

@Composable
private fun DocRow(doc: DocListing, last: Boolean, open: () -> Unit) {
    val colors = CylistTheme.colors
    Column(Modifier.padding(start = 28.dp)) {
        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .clip(RoundedCornerShape(8.dp))
                .clickable(role = Role.Button, onClick = open)
                .padding(horizontal = 4.dp, vertical = 7.dp),
            verticalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            Text(doc.title, color = colors.ink, fontSize = 14.5.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, lineHeight = 19.sp)
            Byline(doc.author, doc.updatedAt, small = true, prefix = "")
        }
    }
    if (last) Rule(Modifier.padding(top = 6.dp))
}

// Reading a doc ---------------------------------------------------------------

@Composable
private fun ReaderPage(
    state: DocsState,
    page: DocsPage.Reading,
    pageUrl: String,
    back: () -> Unit,
    share: () -> Unit,
    retry: () -> Unit,
) {
    val open = state.reading?.takeIf { it.doc.id == page.docId }
    Column(Modifier.fillMaxSize()) {
        PageBar(if (page.fromAnswer) "Answer" else "Docs", back, share, "Share a link to this doc")
        when {
            open == null && state.readingError != null -> Message(state.readingError, retry = retry)
            open == null -> Message("Loading…")
            else -> {
                val section = if (page.fromAnswer) focusSection(state.asked?.answer, open.doc.id) else null
                val mark = remember(open, section) { section?.let { open.markdown.sectionMark(it) } }
                Reader(open, sectionLabel(state.tree, open.doc.section.value), pageUrl, mark?.blockId, mark?.topIndex)
            }
        }
    }
}

@Composable
private fun Reader(open: OpenDoc, sectionLabel: String, pageUrl: String, markedId: Int?, markedTop: Int?) {
    val colors = CylistTheme.colors
    val list = rememberLazyListState()
    val scope = rememberCoroutineScope()
    var tocOpen by rememberSaveable(open.doc.id) { mutableStateOf(false) }
    val doc = open.doc
    val blocks = open.markdown.blocks
    val contents = open.markdown.contents
    val context = remember(pageUrl, markedId) { MarkdownContext(pageUrl, markedId) }
    // Rows before the doc's own blocks: crumb, title, byline and, with two or more headings, "On this page".
    val headRows = if (contents.size >= 2) 4 else 3

    LaunchedEffect(doc.id, markedTop) {
        if (markedTop != null) list.scrollToItem(headRows + markedTop, scrollOffset = -48)
    }

    LazyColumn(Modifier.fillMaxSize(), state = list) {
        item {
            Text(
                "$sectionLabel › ${doc.topicName}",
                modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 4.dp),
                color = colors.ink3,
                fontSize = 12.sp,
                fontFamily = Figtree,
            )
        }
        item {
            Text(
                doc.title,
                modifier = Modifier.padding(horizontal = 16.dp).padding(bottom = 8.dp).semantics { heading() },
                style = titleStyle(24.sp),
            )
        }
        item {
            Box(Modifier.padding(horizontal = 16.dp).padding(bottom = 14.dp)) {
                Byline(doc.author, doc.updatedAt, small = false, prefix = "Updated ")
            }
        }
        if (contents.size >= 2) {
            item {
                Contents(contents, tocOpen, toggle = { tocOpen = !tocOpen }) { index ->
                    scope.launch { list.animateScrollToItem(headRows + index) }
                }
            }
        }
        if (doc.body.isBlank()) {
            item { Message("This doc is empty.") }
        }
        itemsIndexed(blocks, key = { _, block -> block.id }) { _, block ->
            MarkdownBlock(block, context, Modifier.padding(horizontal = 16.dp))
        }
        item { Spacer(Modifier.height(24.dp)) }
    }
}

@Composable
private fun Contents(contents: List<Pair<String, Int>>, open: Boolean, toggle: () -> Unit, jump: (Int) -> Unit) {
    val colors = CylistTheme.colors
    Column(
        Modifier
            .padding(horizontal = 16.dp)
            .padding(bottom = 18.dp)
            .fillMaxWidth()
            .clip(RoundedCornerShape(10.dp))
            .background(colors.paper)
            .border(BorderStroke(1.dp, colors.line), RoundedCornerShape(10.dp)),
    ) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 44.dp)
                .clickable(role = Role.Button, onClick = toggle)
                .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }
                .padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("On this page", modifier = Modifier.weight(1f), color = colors.ink2, fontSize = 13.5.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            Icon(CylistIcons.ChevronDown, null, tint = colors.ink3, modifier = Modifier.size(16.dp).rotate(if (open) 180f else 0f))
        }
        if (open) {
            Column(Modifier.padding(start = 12.dp, end = 12.dp, bottom = 8.dp)) {
                contents.forEach { (title, index) ->
                    Rule()
                    Text(
                        title,
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 40.dp)
                            .clickable(role = Role.Button) { jump(index) }
                            .padding(vertical = 10.dp),
                        color = colors.ink,
                        fontSize = 14.sp,
                        fontFamily = Figtree,
                    )
                }
            }
        }
    }
}

// An answer -------------------------------------------------------------------

@Composable
private fun AnswerPage(
    state: DocsState,
    question: String,
    pageUrl: String,
    back: () -> Unit,
    share: () -> Unit,
    open: (UUID) -> Unit,
    retry: () -> Unit,
) {
    val colors = CylistTheme.colors
    val asked = state.asked?.takeIf { it.question == question }
    val answer = asked?.answer
    Column(Modifier.fillMaxSize()) {
        PageBar("Docs", back, share, "Share a link to this answer")
        LazyColumn(Modifier.fillMaxSize().padding(horizontal = 16.dp)) {
            item {
                Text("Asked the docs", modifier = Modifier.padding(top = 16.dp, bottom = 4.dp), color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
                Text(question, modifier = Modifier.padding(bottom = 8.dp).semantics { heading() }, style = titleStyle(24.sp))
            }
            when {
                asked == null || asked.pending -> item {
                    Text(
                        "Finding the section that answers it — a few seconds, while jev reads the likely ones.",
                        color = colors.ink2,
                        fontSize = 15.sp,
                        fontFamily = Figtree,
                        lineHeight = 23.sp,
                    )
                }
                answer == null -> item {
                    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(asked.error ?: "The docs could not be asked.", color = colors.blocked, fontSize = 15.sp, fontFamily = Figtree)
                        TextButton(onClick = retry) { Text("Try again", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
                    }
                }
                else -> answerRows(answer, pageUrl, open)
            }
            item { Spacer(Modifier.height(24.dp)) }
        }
    }
}

private fun LazyListScope.answerRows(answer: DocAnswer, pageUrl: String, open: (UUID) -> Unit) {
    val isAnswered = answered(answer)
    item {
        StatusLine(answer.status, if (isAnswered) percent(answer.found?.relevance) else null)
    }
    val found = answer.found
    if (found != null && isAnswered) {
        item { Found(found, null, pageUrl, open) }
    }
    if (!isAnswered) {
        item {
            Text(
                missExplained(answer),
                modifier = Modifier.padding(bottom = 18.dp),
                color = CylistTheme.colors.ink2,
                fontSize = 15.sp,
                lineHeight = 23.sp,
                fontFamily = Figtree,
            )
        }
    }
    answer.also?.let { also -> item { Found(also, "The other side of it", pageUrl, open) } }
    val others = weighed(answer)
    if (others.isNotEmpty()) {
        item { SubHeading(weighedHeading(answer)) }
        itemsIndexed(others, key = { index, other -> "weighed-$index-${other.docId}" }) { _, other ->
            WeighedRow(other) { open(other.docId) }
        }
    }
    routing(answer)?.let { reason -> item { Routed(reason) } }
}

@Composable
private fun StatusLine(status: DocAnswer.Status, relevance: String?) {
    val colors = CylistTheme.colors
    val tone = when (statusTone(status)) {
        Tone.Ok -> colors.ok
        Tone.Hold -> colors.hold
        Tone.Blocked -> colors.blocked
    }
    Row(
        Modifier.padding(bottom = 14.dp).semantics(mergeDescendants = true) { },
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(7.dp).clip(CircleShape).background(tone))
        Text(statusLabel(status), color = tone, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
        if (relevance != null) Text("· $relevance", color = colors.ink3, fontSize = 13.sp, fontFamily = Figtree)
    }
}

@Composable
private fun Found(found: SectionFound, heading: String?, pageUrl: String, open: (UUID) -> Unit) {
    val colors = CylistTheme.colors
    val markdown = remember(found.text) { MarkdownDoc.parse(found.text) }
    Column(Modifier.padding(bottom = 20.dp)) {
        if (heading != null) SubHeading(heading)
        Column(
            Modifier
                .fillMaxWidth()
                .clip(RoundedCornerShape(10.dp))
                .background(colors.paper)
                .border(BorderStroke(1.dp, colors.line), RoundedCornerShape(10.dp))
                .padding(start = 14.dp, end = 14.dp, top = 12.dp, bottom = 4.dp),
        ) {
            Row(Modifier.padding(bottom = 8.dp), verticalAlignment = Alignment.Top, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text(
                    crumbs(found).joinToString(" › "),
                    modifier = Modifier.weight(1f).padding(top = 2.dp),
                    color = colors.ink3,
                    fontSize = 12.sp,
                    lineHeight = 17.sp,
                    fontFamily = Figtree,
                )
                Box(
                    Modifier
                        .heightIn(min = 34.dp)
                        .clip(RoundedCornerShape(8.dp))
                        .border(BorderStroke(1.dp, colors.line2), RoundedCornerShape(8.dp))
                        .clickable(role = Role.Button) { open(found.docId) }
                        .padding(horizontal = 10.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    Text("Open doc", color = colors.ink, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                }
            }
            if (found.text.isBlank()) {
                Text("This section is empty.", modifier = Modifier.padding(bottom = 12.dp), color = colors.ink3, fontSize = 14.sp, fontFamily = Figtree)
            } else {
                MarkdownColumn(markdown, MarkdownContext(pageUrl, marked = null))
            }
        }
    }
}

@Composable
private fun WeighedRow(other: Weighed, open: () -> Unit) {
    val colors = CylistTheme.colors
    val steps = crumbs(other.path, other.section)
    Column {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 48.dp)
                .clickable(role = Role.Button, onClick = open)
                .padding(horizontal = 4.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(10.dp),
        ) {
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(1.dp)) {
                Text(steps.last(), color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                if (steps.size > 1) {
                    Text(steps.dropLast(1).joinToString(" › "), color = colors.ink3, fontSize = 12.sp, fontFamily = Figtree)
                }
            }
            percent(other.relevance)?.let { Text(it.removeSuffix(" relevant"), color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono) }
        }
        Rule()
    }
}

@Composable
private fun Routed(reason: String) {
    val colors = CylistTheme.colors
    var open by rememberSaveable { mutableStateOf(false) }
    Column(Modifier.padding(top = 12.dp)) {
        Row(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 44.dp)
                .clickable(role = Role.Button) { open = !open }
                .semantics { stateDescription = if (open) "Expanded" else "Collapsed" }
                .padding(horizontal = 2.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            Icon(DocsIcons.ChevronRight, null, tint = colors.ink3, modifier = Modifier.size(12.dp).rotate(if (open) 90f else 0f))
            Text("How it was routed", color = colors.ink2, fontSize = 13.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
        }
        if (open) Text(reason, modifier = Modifier.padding(start = 2.dp, bottom = 10.dp), color = colors.ink3, fontSize = 13.sp, lineHeight = 21.sp, fontFamily = Figtree)
    }
}

// Shared pieces -----------------------------------------------------------------

@Composable
private fun PageBar(backLabel: String, back: () -> Unit, share: () -> Unit, shareLabel: String) {
    val colors = CylistTheme.colors
    Column {
        Row(
            Modifier.fillMaxWidth().padding(start = 4.dp, end = 6.dp, top = 14.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Row(
                Modifier
                    .heightIn(min = 44.dp)
                    .clip(RoundedCornerShape(8.dp))
                    .clickable(role = Role.Button, onClick = back)
                    .semantics(mergeDescendants = true) { contentDescription = "Back to $backLabel" }
                    .padding(start = 4.dp, end = 10.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(2.dp),
            ) {
                Icon(CylistIcons.ChevronLeft, null, tint = colors.ink2, modifier = Modifier.size(20.dp))
                Text(backLabel, color = colors.ink2, fontSize = 15.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            }
            Spacer(Modifier.weight(1f))
            IconAction(DocsIcons.Share, shareLabel, share)
        }
        Rule()
    }
}

@Composable
private fun IconAction(icon: ImageVector, label: String, onClick: () -> Unit) {
    Box(
        Modifier
            .size(44.dp)
            .clip(RoundedCornerShape(8.dp))
            .clickable(role = Role.Button, onClick = onClick)
            .semantics { contentDescription = label },
        contentAlignment = Alignment.Center,
    ) {
        Icon(icon, null, tint = CylistTheme.colors.ink2, modifier = Modifier.size(19.dp))
    }
}

@Composable
private fun Byline(author: PersonRead?, updatedAt: String, small: Boolean, prefix: String) {
    val colors = CylistTheme.colors
    val size: TextUnit = if (small) 12.sp else 13.sp
    val stamp = "$prefix${formatStamp(updatedAt)}"
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        if (author != null) Avatar(author, if (small) 16.dp else 22.dp, if (small) 8.sp else 10.sp)
        Text(
            if (author != null) "${author.name} · $stamp" else stamp,
            color = colors.ink3,
            fontSize = size,
            fontFamily = Figtree,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}

@Composable
private fun Avatar(person: PersonRead, size: Dp, textSize: TextUnit) {
    val colour = person.colour.removePrefix("#").takeIf { it.length == 6 }?.toLongOrNull(16)?.let { Color(0xFF000000 or it) }
    Box(
        Modifier.size(size).clip(CircleShape).background(colour ?: CylistTheme.colors.ink3),
        contentAlignment = Alignment.Center,
    ) {
        // Its own line height: the theme's body line height would push two letters off the circle's middle.
        Text(initialsOf(person.name), color = Color.White, fontSize = textSize, lineHeight = textSize, fontWeight = FontWeight.Bold, fontFamily = Figtree)
    }
}

@Composable
private fun SubHeading(text: String) {
    val colors = CylistTheme.colors
    Column(Modifier.padding(bottom = 4.dp).semantics { heading() }) {
        Text(text, modifier = Modifier.padding(horizontal = 2.dp).padding(bottom = 6.dp), color = colors.ink, fontSize = 14.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
        Rule()
    }
}

@Composable
private fun Rule(modifier: Modifier = Modifier) {
    Box(modifier.fillMaxWidth().height(1.dp).background(CylistTheme.colors.line))
}

@Composable
private fun Message(text: String, retry: (() -> Unit)? = null) {
    val colors = CylistTheme.colors
    Column(
        Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 40.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text, color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, textAlign = TextAlign.Center)
        if (retry != null) TextButton(onClick = retry) { Text("Try again", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold) }
    }
}

@Composable
private fun titleStyle(size: TextUnit) = TextStyle(
    fontFamily = Figtree,
    fontSize = size,
    lineHeight = size * 1.25f,
    fontWeight = FontWeight.Bold,
    letterSpacing = (-0.015).em,
    color = CylistTheme.colors.ink,
)
