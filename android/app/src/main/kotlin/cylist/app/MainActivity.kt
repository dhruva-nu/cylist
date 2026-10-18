package cylist.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
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
import androidx.compose.foundation.selection.selectable
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
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
import cylist.app.session.Session
import cylist.app.session.SessionState
import cylist.app.signin.SignInScreen
import cylist.app.signin.SignInViewModel
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.ui.CylistIcons
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val app = application as CylistApp
        setContent {
            CylistTheme { Root(app) }
        }
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
            AppShell(current.session, signingOut) {
                signingOut = true
                // On the app's scope, so turning the phone mid-way does not
                // leave the token half signed out.
                app.scope.launch { app.sessions.signOut() }
            }
        }
    }
}

/** The five places the tab bar goes to. Only Board is built so far; the rest are their own cards. */
enum class Tab(val label: String, val icon: ImageVector, val card: String?) {
    Board("Board", CylistIcons.Board, null),
    Today("Today", CylistIcons.Today, "CYLIST-105"),
    Docs("Docs", CylistIcons.Docs, null),
    People("People", CylistIcons.People, null),
    More("More", CylistIcons.More, null),
}

@Composable
fun AppShell(session: Session, signingOut: Boolean, signOut: () -> Unit) {
    var tab by rememberSaveable { mutableStateOf(Tab.Board) }
    // Keyed by the token, so signing in again (perhaps elsewhere) starts a fresh board.
    val board: BoardViewModel = viewModel(key = "board-${session.account.tokenId}") { BoardViewModel(session) }
    val docs: DocsViewModel = viewModel(key = "docs-${session.account.tokenId}") { DocsViewModel(session) }
    Column(Modifier.fillMaxSize().background(CylistTheme.colors.bg)) {
        Box(Modifier.weight(1f)) {
            when (tab) {
                Tab.Board -> BoardScreen(board)
                Tab.Docs -> {
                    // Docs follows the project the board is on.
                    val onBoard by board.state.collectAsStateWithLifecycle()
                    DocsScreen(docs, onBoard.project?.key, session.server)
                }
                Tab.More -> MoreScreen(session.account, signingOut, signOut)
                else -> NotYet(tab)
            }
        }
        TabBar(tab) { tab = it }
    }
}

@Composable
fun TabBar(current: Tab, select: (Tab) -> Unit) {
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
                    Icon(tab.icon, null, tint = tone, modifier = Modifier.size(20.dp))
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
