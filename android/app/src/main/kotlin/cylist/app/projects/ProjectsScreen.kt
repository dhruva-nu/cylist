package cylist.app.projects

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import cylist.app.board.BoardState
import cylist.app.board.hex
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons

/** The Projects tab, the app's first screen: every project you are a member of; tapping one opens its board. */
@Composable
fun ProjectsScreen(state: BoardState, open: (String) -> Unit) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxSize().statusBarsPadding()) {
        Text(
            "Projects",
            color = colors.ink,
            fontSize = 22.sp,
            fontWeight = FontWeight.Bold,
            fontFamily = Figtree,
            modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 16.dp, bottom = 8.dp),
        )
        when {
            state.error != null && state.projects.isEmpty() -> Message(state.error)
            state.loading && state.projects.isEmpty() -> Message("Loading projects…")
            state.projects.isEmpty() -> Message("You are not on any project yet.")
            else -> LazyColumn(Modifier.fillMaxSize()) {
                items(state.projects, key = { it.key }) { project ->
                    Row(
                        Modifier
                            .fillMaxWidth()
                            .heightIn(min = 60.dp)
                            .clickable(role = Role.Button) { open(project.key) }
                            .padding(horizontal = 16.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(12.dp),
                    ) {
                        Box(
                            Modifier.size(32.dp).clip(RoundedCornerShape(8.dp)).background(hex(project.colour, colors.ink3)),
                            contentAlignment = Alignment.Center,
                        ) {
                            Text(project.key.take(1), color = Color.White, fontSize = 14.sp, fontWeight = FontWeight.ExtraBold, fontFamily = Figtree)
                        }
                        Column(Modifier.weight(1f)) {
                            Text(project.name, color = colors.ink, fontSize = 16.sp, fontWeight = FontWeight.SemiBold, fontFamily = Figtree, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            Text(project.key, color = colors.ink3, fontSize = 12.sp, fontFamily = PlexMono)
                        }
                        Icon(CylistIcons.ChevronRight, null, tint = colors.ink3, modifier = Modifier.size(16.dp))
                    }
                }
            }
        }
    }
}

@Composable
private fun Message(text: String) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().padding(horizontal = 24.dp, vertical = 40.dp), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(text, color = colors.ink2, fontSize = 14.sp, fontFamily = Figtree, textAlign = TextAlign.Center)
    }
}
