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
import androidx.lifecycle.viewmodel.compose.viewModel
import cylist.app.board.BoardScreen
import cylist.app.board.BoardViewModel
import cylist.app.session.Session
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.ui.CylistIcons

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val session = Session.current()
        setContent {
            CylistTheme {
                if (session == null) SignedOut() else AppShell(session)
            }
        }
    }
}

/** The five places the tab bar goes to. Only Board is built so far; the rest are their own cards. */
enum class Tab(val label: String, val icon: ImageVector, val card: String?) {
    Board("Board", CylistIcons.Board, null),
    Today("Today", CylistIcons.Today, "CYLIST-105"),
    Docs("Docs", CylistIcons.Docs, "CYLIST-106"),
    People("People", CylistIcons.People, null),
    More("More", CylistIcons.More, null),
}

@Composable
fun AppShell(session: Session) {
    var tab by rememberSaveable { mutableStateOf(Tab.Board) }
    val board: BoardViewModel = viewModel { BoardViewModel(session) }
    Column(Modifier.fillMaxSize().background(CylistTheme.colors.bg)) {
        Box(Modifier.weight(1f)) {
            when (tab) {
                Tab.Board -> BoardScreen(board)
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

/** No session yet. Sign-in is CYLIST-102; until then a debug build is given one at build time. */
@Composable
private fun SignedOut() {
    val colors = CylistTheme.colors
    Box(Modifier.fillMaxSize().background(colors.bg).padding(32.dp), contentAlignment = Alignment.Center) {
        Text(
            "Not signed in. Put cylist.server and cylist.token in android/local.properties and rebuild.",
            color = colors.ink2,
            fontSize = 14.sp,
            fontFamily = Figtree,
            textAlign = TextAlign.Center,
        )
    }
}
