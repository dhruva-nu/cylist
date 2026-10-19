package cylist.app.push

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleStartEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import cylist.api.generated.models.AgentFeedEntryRead
import cylist.api.generated.models.AgentFeedKind
import cylist.api.generated.models.AgentPromptRead
import cylist.api.generated.models.AgentPromptStatus
import cylist.api.generated.models.AgentSessionRead
import cylist.api.generated.models.AgentSessionState
import cylist.app.session.Session
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.theme.Radii
import cylist.app.theme.TextSizes
import cylist.app.ui.CylistIcons

/**
 * One card's agent session: what tapping an agent notification opens. After
 * the design's "Agent · watching it live" artboard: the card, the session's
 * state and what it is doing now, its open question with the options to
 * answer it, and its feed, newest first.
 *
 * The full card (comments, checklist, sub-tasks) is the task detail screen's
 * job (CYLIST-104); this is the part a notification is about.
 */
@Composable
fun AgentSessionScreen(session: Session, link: CardLink, close: () -> Unit) {
    val viewModel: AgentSessionViewModel = viewModel(key = "agent-${session.account.tokenId}-${link.toUri()}") {
        AgentSessionViewModel(session, link)
    }
    val state by viewModel.state.collectAsStateWithLifecycle()
    LifecycleStartEffect(viewModel) {
        viewModel.connect()
        onStopOrDispose { viewModel.disconnect() }
    }
    BackHandler(onBack = close)
    val colors = CylistTheme.colors

    Column(Modifier.fillMaxSize().background(colors.bg).statusBarsPadding().navigationBarsPadding()) {
        Row(Modifier.fillMaxWidth().padding(start = 4.dp, end = 4.dp, top = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = close) {
                Icon(CylistIcons.ChevronLeft, contentDescription = "Back to the board", tint = colors.ink2, modifier = Modifier.size(20.dp))
            }
            Text(
                "${state.reference} · agent",
                color = colors.ink3,
                fontSize = TextSizes.md,
                fontFamily = PlexMono,
                textAlign = TextAlign.Center,
                modifier = Modifier.weight(1f),
            )
            Box(Modifier.width(48.dp))
        }

        Column(Modifier.fillMaxWidth().padding(start = 18.dp, end = 18.dp, top = 4.dp, bottom = 12.dp)) {
            Text(
                state.title.ifBlank { if (state.loading) "Loading…" else state.reference },
                color = colors.ink,
                fontSize = TextSizes.xl2,
                fontWeight = FontWeight.Bold,
                fontFamily = Figtree,
            )
            val current = state.session
            if (current != null) {
                Who(current)
                Now(current)
            } else if (!state.loading) {
                Text(
                    "No agent is on this card now.",
                    color = colors.ink3,
                    fontSize = TextSizes.base,
                    fontFamily = Figtree,
                    modifier = Modifier.padding(top = 8.dp),
                )
            }
            state.error?.let {
                Text(it, color = colors.blocked, fontSize = TextSizes.md, fontFamily = Figtree, modifier = Modifier.padding(top = 8.dp))
            }
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))

        Row(Modifier.padding(start = 18.dp, end = 18.dp, top = 12.dp, bottom = 6.dp), verticalAlignment = Alignment.Bottom) {
            Text("What it's doing", color = colors.ink, fontSize = TextSizes.base, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            Text(
                "  newest first · ${state.feed.size} lines",
                color = colors.ink3,
                fontSize = TextSizes.sm,
                fontFamily = Figtree,
            )
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 18.dp)) {
            items(state.feed, key = { it.id }) { FeedLine(it) }
        }

        val prompt = state.session?.prompt?.takeIf { it.status == AgentPromptStatus.`open` }
        if (prompt != null) Ask(prompt, state.answering) { key -> viewModel.answer(prompt.id, key) }
    }
}

private enum class Tone { Working, NeedsYou, Finished }

private fun toneOf(session: AgentSessionRead): Tone = when (session.state) {
    AgentSessionState.waiting -> Tone.NeedsYou
    AgentSessionState.done -> Tone.Finished
    else -> Tone.Working
}

@Composable
private fun toneColor(tone: Tone): Color = when (tone) {
    Tone.Working -> CylistTheme.colors.agent
    Tone.NeedsYou -> CylistTheme.colors.hold
    Tone.Finished -> CylistTheme.colors.ok
}

@Composable
private fun Who(session: AgentSessionRead) {
    val colors = CylistTheme.colors
    val tone = toneOf(session)
    Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.Top) {
        Column(Modifier.weight(1f)) {
            Text(session.actorLabel, color = colors.ink, fontSize = TextSizes.base, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            Text(
                listOfNotNull("agent", session.clientName, "since ${clock(session.startedAt)}").joinToString(" · "),
                color = colors.ink3,
                fontSize = TextSizes.sm,
                fontFamily = Figtree,
            )
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(7.dp).background(toneColor(tone), CircleShape))
            Text(
                when (tone) {
                    Tone.Working -> "Working"
                    Tone.NeedsYou -> "Needs you"
                    Tone.Finished -> "Finished"
                },
                color = toneColor(tone),
                fontSize = TextSizes.md,
                fontWeight = FontWeight.SemiBold,
                fontFamily = Figtree,
                modifier = Modifier.padding(start = 6.dp),
            )
        }
    }
}

@Composable
private fun Now(session: AgentSessionRead) {
    val colors = CylistTheme.colors
    val tone = toneOf(session)
    val (label, text) = when (tone) {
        Tone.NeedsYou -> "Asks" to (session.prompt?.text ?: session.note ?: "Waiting for your reply")
        Tone.Finished -> "Done" to "The session ended."
        Tone.Working -> "Now" to (session.note ?: "Working")
    }
    Row(Modifier.fillMaxWidth().padding(top = 12.dp).heightIn(min = 22.dp)) {
        Box(Modifier.width(2.dp).heightIn(min = 22.dp).background(toneColor(tone)))
        Text(label, color = colors.ink3, fontSize = TextSizes.sm, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, modifier = Modifier.padding(start = 10.dp, top = 1.dp))
        Text(text, color = colors.ink, fontSize = TextSizes.base, fontFamily = Figtree, modifier = Modifier.padding(start = 10.dp))
    }
}

@Composable
private fun FeedLine(line: AgentFeedEntryRead) {
    val colors = CylistTheme.colors
    val code = line.kind != AgentFeedKind.said
    val failed = line.kind == AgentFeedKind.error
    Column {
        Row(Modifier.fillMaxWidth().padding(vertical = 9.dp)) {
            Text(
                labelOf(line.kind),
                color = if (failed) colors.blocked else colors.ink3,
                fontSize = TextSizes.sm,
                fontWeight = FontWeight.SemiBold,
                fontFamily = Figtree,
                modifier = Modifier.width(48.dp),
            )
            Text(
                line.text,
                color = if (failed) colors.blocked else if (code) colors.ink2 else colors.ink,
                fontSize = if (code) TextSizes.md else TextSizes.base,
                fontFamily = if (code) PlexMono else Figtree,
                modifier = Modifier.weight(1f),
            )
            Text(clock(line.at), color = colors.ink3, fontSize = TextSizes.xs, fontFamily = PlexMono, modifier = Modifier.padding(start = 8.dp, top = 2.dp))
        }
        Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
    }
}

@Composable
private fun Ask(prompt: AgentPromptRead, answering: Boolean, answer: (String) -> Unit) {
    val colors = CylistTheme.colors
    Column(
        Modifier
            .fillMaxWidth()
            .background(colors.paper)
            .padding(start = 16.dp, end = 16.dp, top = 12.dp, bottom = 16.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Text("Waiting: a question for you", color = colors.hold, fontSize = TextSizes.sm, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
        Text(prompt.text, color = colors.ink, fontSize = TextSizes.lg, fontFamily = Figtree)
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            prompt.options.take(MAX_OPTIONS).forEach { option ->
                OutlinedButton(
                    onClick = { answer(option.key) },
                    enabled = !answering,
                    border = BorderStroke(1.dp, colors.line2),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = colors.ink),
                    shape = RoundedCornerShape(Radii.control),
                    modifier = Modifier.weight(1f).heightIn(min = 44.dp),
                ) {
                    Text(option.label, fontFamily = Figtree, fontWeight = FontWeight.SemiBold, maxLines = 1)
                }
            }
        }
    }
}

private const val MAX_OPTIONS = 3

private fun labelOf(kind: AgentFeedKind): String = when (kind) {
    AgentFeedKind.said -> "Said"
    AgentFeedKind.read -> "Read"
    AgentFeedKind.edit -> "Edit"
    AgentFeedKind.run -> "Ran"
    AgentFeedKind.output -> "Got"
    AgentFeedKind.error -> "Failed"
    else -> "Tool"
}

/** `14:32` from an ISO timestamp, in the phone's own zone. */
private fun clock(iso: String): String = try {
    java.time.OffsetDateTime.parse(iso)
        .atZoneSameInstant(java.time.ZoneId.systemDefault())
        .toLocalTime()
        .format(java.time.format.DateTimeFormatter.ofPattern("HH:mm"))
} catch (_: java.time.format.DateTimeParseException) {
    ""
}
