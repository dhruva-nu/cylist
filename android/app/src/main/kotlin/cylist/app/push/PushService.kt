package cylist.app.push

import com.google.firebase.messaging.FirebaseMessagingService
import com.google.firebase.messaging.RemoteMessage
import cylist.app.CylistApp
import kotlinx.coroutines.launch

/**
 * Where FCM hands this app its messages, with the app open, in the background
 * or swiped away: the server only sends data messages, so this always runs and
 * the app draws the notification itself.
 */
class PushService : FirebaseMessagingService() {
    override fun onMessageReceived(message: RemoteMessage) = deliver(application as CylistApp, message.data)

    override fun onNewToken(token: String) {
        val app = application as CylistApp
        val session = app.sessions.current() ?: return
        app.scope.launch { app.push.register(session, token) }
    }

    companion object {
        /** Show one push's data. Also what the debug build's simulated pushes go through. */
        fun deliver(app: CylistApp, data: Map<String, String>) {
            // A push that arrives after signing out (sent before the server heard) is dropped.
            if (app.sessions.current() == null) return
            val update = AgentUpdate.parse(data) ?: return
            app.notifier.show(update)
        }
    }
}
