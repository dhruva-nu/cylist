package cylist.app

import android.content.Intent
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.isImeVisible
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.wrapContentSize
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import cylist.app.board.BoardScreen
import cylist.app.board.BoardViewModel
import cylist.app.docs.DocsScreen
import cylist.app.docs.DocsViewModel
import cylist.app.more.MoreScreen
import cylist.app.push.AgentSessionScreen
import cylist.app.push.AskForNotifications
import cylist.app.push.CardLink
import cylist.app.session.Session
import cylist.app.session.SessionState
import cylist.app.signin.SignInScreen
import cylist.app.signin.SignInViewModel
import cylist.app.task.TaskDetailRoute
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.today.TodayScreen
import cylist.app.today.TodayViewModel
import cylist.app.ui.CylistIcons
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as CylistApp
        if (savedInstanceState == null) opened(intent)
        setContent {
            CylistTheme { Root(app) }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        opened(intent)
    }

    /** An agent notification's tap carries the card to open (CYLIST-107). */
    private fun opened(intent: Intent?) {
        CardLink.parse(intent?.dataString)?.let { (application as CylistApp).openCard.value = it }
    }
}

/** Sign-in while signed out, the app while signed in; [Sessions] decides which. */
@Composable
private fun Root(app: CylistApp) {
    val state by app.sessions.state.collectAsStateWithLifecycle()
    when (val current = state) {
        is SessionState.SignedOut -> {
            val signIn: SignInViewModel = viewModel { SignInViewModel(app.sessions, app.accounts, app.cleartextHosts) }
            SignInScreen(signIn, current.notice, app.sessions.tokenName)
        }
        is SessionState.SignedIn -> {
            var signingOut by remember(current.session) { mutableStateOf(false) }
            val card by app.openCard.collectAsStateWithLifecycle()
            AppShell(current.session, signingOut, card, closeCard = { app.openCard.value = null }) {
                signingOut = true
                // On the app's scope, so turning the phone mid-way does not
                // leave the token half signed out.
                app.scope.launch { app.sessions.signOut() }
            }
        }
    }
}

/** The five places the tab bar goes to. Those not built yet name the card that brings them. */
enum class Tab(val label: String, val icon: ImageVector, val card: String?) {
    Board("Board", CylistIcons.Board, null),
    Today("Today", CylistIcons.Today, null),
    Docs("Docs", CylistIcons.Docs, null),
    People("People", CylistIcons.People, null),
    More("More", CylistIcons.More, null),
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun AppShell(
    session: Session,
    signingOut: Boolean,
    card: CardLink? = null,
    closeCard: () -> Unit = {},
    signOut: () -> Unit,
) {
    var tab by rememberSaveable { mutableStateOf(Tab.Board) }
    // Keyed by the token, so signing in again (perhaps elsewhere) starts a fresh board.
    val board: BoardViewModel = viewModel(key = "board-${session.account.tokenId}") { BoardViewModel(session) }
    // Today plans on the project the Board is on, and its count rides on the tab.
    val today: TodayViewModel = viewModel(key = "today-${session.account.tokenId}") { TodayViewModel(session) }
    val boardState by board.state.collectAsStateWithLifecycle()
    val todayState by today.state.collectAsStateWithLifecycle()
    LaunchedEffect(boardState.project?.key) { boardState.project?.key?.let(today::setProject) }
    val docs: DocsViewModel = viewModel(key = "docs-${session.account.tokenId}") { DocsViewModel(session) }
    AskForNotifications()
    if (card != null) {
        // An agent notification was tapped: its card's session, over the tabs.
        AgentSessionScreen(session, card, closeCard)
        return
    }
    // The card open over the board (CYLIST-104), as "KEY|KEY-41"; it takes the whole screen.
    var openTask by rememberSaveable { mutableStateOf<String?>(null) }
    openTask?.let { opened ->
        TaskDetailRoute(session, opened) { openTask = null }
        return
    }
    // Above the keyboard, so a box being typed in (a to-do on Today) is never under it.
    Column(Modifier.fillMaxSize().background(CylistTheme.colors.bg).imePadding()) {
        Box(Modifier.weight(1f)) {
            when (tab) {
                Tab.Board -> BoardScreen(board, openTask = { key, ref -> openTask = "$key|$ref" })
                Tab.Today -> TodayScreen(today)
                // Docs follows the project the board is on.
                Tab.Docs -> DocsScreen(docs, boardState.project?.key, session.server)
                Tab.More -> MoreScreen(session.account, signingOut, signOut)
                else -> NotYet(tab)
            }
        }
        // Out of the way while typing, so the box and what it offers have the room.
        if (!WindowInsets.isImeVisible) TabBar(tab, badges = mapOf(Tab.Today to TabBadge(todayState.todayOpen, warn = todayState.lateUnplanned != null))) { tab = it }
    }
}

/** A count on a tab: the to-dos left today on Today, red while a late card of yours is off the plan. */
data class TabBadge(val count: Int, val warn: Boolean = false)

@Composable
fun TabBar(current: Tab, badges: Map<Tab, TabBadge> = emptyMap(), select: (Tab) -> Unit) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().background(colors.paper)) {
        Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
        Row(Modifier.fillMaxWidth().navigationBarsPadding().padding(start = 4.dp, end = 4.dp, top = 4.dp, bottom = 6.dp)) {
            Tab.entries.forEach { tab ->
                val on = tab == current
                Column(
                    Modifier
                        .weight(1f)
                        .heightIn(min = 48.dp)
                        .selectable(selected = on, role = Role.Tab) { select(tab) }
                        .padding(top = 7.dp, bottom = 4.dp, start = 2.dp, end = 2.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.spacedBy(3.dp),
                ) {
                    val tone = if (on) colors.ink else colors.ink3
                    // The icon's own size, so a badge hanging off its corner moves nothing.
                    Box(Modifier.size(20.dp)) {
                        Icon(tab.icon, null, tint = tone, modifier = Modifier.size(20.dp))
                        badges[tab]?.takeIf { it.count > 0 || it.warn }?.let { badge ->
                            Text(
                                "${badge.count}",
                                color = if (badge.warn) colors.blocked else colors.ink2,
                                fontSize = 11.sp,
                                fontWeight = FontWeight.SemiBold,
                                fontFamily = PlexMono,
                                modifier = Modifier.wrapContentSize(Alignment.TopStart, unbounded = true).offset(x = 22.dp, y = (-4).dp),
                            )
                        }
                    }
                    Text(tab.label, color = tone, fontSize = 11.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
                }
            }
        }
    }
}

@Composable
private fun NotYet(tab: Tab) {
    val colors = CylistTheme.colors
    Box(Modifier.fillMaxSize().statusBarsPadding().padding(32.dp), contentAlignment = Alignment.Center) {
        Text(
            tab.card?.let { "${tab.label} comes with $it." } ?: "${tab.label} is not built yet.",
            color = colors.ink3,
            fontSize = 14.sp,
            fontFamily = Figtree,
            textAlign = TextAlign.Center,
        )
    }
}
