package cylist.app.push

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import cylist.app.CylistApp
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID

/**
 * Debug builds only: post an agent push as if FCM had delivered it, so the
 * Live Update can be seen without Firebase credentials. Guarded by the DUMP
 * permission in src/debug/AndroidManifest.xml, which adb's shell holds and
 * other apps do not:
 *
 *     adb shell am broadcast -n in.cylist.app.debug/cylist.app.push.DebugPushReceiver \
 *       --es task ATL-12 --es state waiting --es reason question --es note "Sheet 1 or Mar-2026?"
 *
 * Every extra is a key of the server's data message (backend/app/services/push/messages.py);
 * the ones left out get plausible values. The message goes through the same
 * [PushService.deliver] a real one does.
 */
class DebugPushReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val extras = intent.extras ?: return
        val task = extras.getString("task") ?: return
        val now = Instant.now()
        val data = buildMap {
            put("kind", AgentUpdate.KIND)
            put("session_id", UUID.nameUUIDFromBytes(task.toByteArray()).toString())
            put("task_title", "")
            put("project", task.substringBefore('-'))
            put("reason", "")
            put("note", "")
            put("client_name", "")
            put("started_at", now.minus(4, ChronoUnit.MINUTES).toString())
            put("state_changed_at", now.toString())
            extras.keySet().forEach { key -> extras.getString(key)?.let { put(key, it) } }
        }
        PushService.deliver(context.applicationContext as CylistApp, data)
    }
}
