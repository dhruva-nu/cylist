package cylist.app.task

import androidx.compose.runtime.Composable
import androidx.lifecycle.viewmodel.compose.viewModel
import cylist.app.session.Session

/**
 * The Task detail screen for one card, as the shell opens it from the board:
 * [opened] is `"KEY|KEY-41"`, the project and the card's reference, which is
 * all that survives the activity being recreated.
 */
@Composable
fun TaskDetailRoute(session: Session, opened: String, close: () -> Unit) {
    val projectKey = opened.substringBefore('|')
    val ref = opened.substringAfter('|')
    // Keyed by the token as the board is, so signing in again starts afresh, and by the card.
    val model: TaskDetailViewModel = viewModel(key = "task-${session.account.tokenId}-$ref") {
        TaskDetailViewModel(ServerTaskSource(session), projectKey, ref)
    }
    TaskDetailScreen(model, webUrl = "${session.server.trimEnd('/')}/p/$projectKey/board?q=$ref", close = close)
}
