package cylist.app.push

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.os.Bundle
import androidx.core.app.NotificationCompat
import androidx.core.net.toUri
import cylist.app.MainActivity

/**
 * Posts an [AgentUpdate] as a notification: one per agent session, replaced in
 * place as the session moves from working to needing you to finished.
 *
 * On Android 16 (API 36) the working and waiting states ask to be promoted —
 * `setRequestPromotedOngoing` — which is what makes them Live Updates: a chip
 * in the status bar, and on OxygenOS the camera pill, once Live Alerts are on
 * for Cylist. Before 36 they are ordinary ongoing notifications.
 *
 * Only one of them is promoted at a time, though: the pill shows one, and
 * [AgentPill] picks which and puts the count of running agents in its icon.
 * When that choice moves, the notifications it touched are redrawn quietly.
 * Each notification carries its [AgentUpdate], so the choice can be made again
 * from what is showing, even after the process was killed between pushes.
 *
 * Tapping one opens the app on that card's agent session ([CardLink]).
 */
class AgentNotifier(private val context: Context) {
    private val manager: NotificationManager = context.getSystemService(NotificationManager::class.java)
    private val icons = PillIcons(context)

    /** Make the three channels. Idempotent; called before every post and once at start-up. */
    fun ensureChannels() {
        manager.createNotificationChannels(
            AlertChannel.entries.map { channel ->
                NotificationChannel(channel.id, channel.label, importanceOf(channel)).apply {
                    description = channel.description
                }
            },
        )
    }

    /**
     * Show [update], unless a newer state of the same session is already up:
     * pushes for one session can arrive out of order, and an old "working"
     * must not paper over a newer "needs you".
     */
    fun show(update: AgentUpdate) {
        if (!allowed()) return
        ensureChannels()
        val shown = showing()
        val before = shown[update.sessionId]
        if (before != null && before.update.changedAtMillis > update.changedAtMillis) return
        val live = shown.values.map { it.update }.filter { it.state != AgentState.Done && it.sessionId != update.sessionId } +
            listOfNotNull(update.takeIf { it.state != AgentState.Done })
        val pill = AgentPill.of(live)
        post(update, pill, quiet = false)
        // The pill moved to another session, or its count changed: redraw the
        // ones that changed role, without ringing for a question twice.
        live.filter { it.sessionId != update.sessionId && shown[it.sessionId]?.role != pill.roleOf(it.sessionId) }
            .forEach { post(it, pill, quiet = true) }
    }

    /** Take every agent notification down, as signing out does. */
    fun clear() {
        manager.activeNotifications.filter { it.id == ID }.forEach { manager.cancel(it.tag, it.id) }
    }

    /** A notification of ours that is up, read back: its session's update and its role in the pill. */
    private class Shown(val update: AgentUpdate, val role: String?)

    private fun showing(): Map<String, Shown> = manager.activeNotifications
        .filter { it.id == ID }
        .mapNotNull { posted ->
            val extras = posted.notification.extras
            val data = extras.getBundle(EXTRA_UPDATE) ?: return@mapNotNull null
            val update = AgentUpdate.parse(data.keySet().associateWith { data.getString(it).orEmpty() }) ?: return@mapNotNull null
            update.sessionId to Shown(update, extras.getString(EXTRA_ROLE))
        }
        .toMap()

    private fun post(update: AgentUpdate, pill: AgentPill, quiet: Boolean) {
        manager.notify(update.sessionId, ID, build(update, AgentAlert.of(update), pill, quiet))
    }

    private fun build(update: AgentUpdate, alert: AgentAlert, pill: AgentPill, quiet: Boolean): Notification {
        val leads = pill.leader == update.sessionId
        val link = CardLink(update.task, update.sessionId)
        val open = PendingIntent.getActivity(
            context,
            update.sessionId.hashCode(),
            Intent(context, MainActivity::class.java)
                .setAction(Intent.ACTION_VIEW)
                .setData(link.toUri().toUri())
                .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        // NotificationCompat, because it asks for promotion the way every
        // Android 16 reads it: the platform's own setter arrived a release
        // later (36.1) than the extra it sets, which Compat writes directly.
        val builder = NotificationCompat.Builder(context, alert.channel.id)
            .setSmallIcon(if (leads) icons.of(pill.mark) else icons.plain())
            .setContentTitle(alert.title)
            .setContentText(alert.text)
            .setStyle(NotificationCompat.BigTextStyle().bigText(alert.text))
            .setSubText(update.project.takeIf { it.isNotBlank() })
            .setContentIntent(open)
            .setOngoing(alert.ongoing)
            .setAutoCancel(!alert.ongoing)
            .setOnlyAlertOnce(quiet || alert.channel != AlertChannel.NeedsYou)
            .setCategory(
                when (alert.channel) {
                    AlertChannel.NeedsYou -> NotificationCompat.CATEGORY_REMINDER
                    AlertChannel.Running -> NotificationCompat.CATEGORY_PROGRESS
                    AlertChannel.Finished -> NotificationCompat.CATEGORY_STATUS
                },
            )
            .addExtras(
                Bundle().apply {
                    putBundle(EXTRA_UPDATE, Bundle().apply { update.toData().forEach { (key, value) -> putString(key, value) } })
                    putString(EXTRA_ROLE, pill.roleOf(update.sessionId))
                },
            )
            .setRequestPromotedOngoing(alert.promoted && leads)
        if (leads) alert.chip?.let(builder::setShortCriticalText)
        val since = alert.chronometerSinceMillis
        if (since != null) {
            builder.setWhen(since).setShowWhen(true).setUsesChronometer(true)
        } else {
            builder.setWhen(update.changedAtMillis).setShowWhen(true)
        }
        return builder.build()
    }

    private fun allowed(): Boolean =
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
            context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED

    private fun importanceOf(channel: AlertChannel): Int = when (channel) {
        AlertChannel.NeedsYou -> NotificationManager.IMPORTANCE_HIGH
        AlertChannel.Running -> NotificationManager.IMPORTANCE_LOW
        AlertChannel.Finished -> NotificationManager.IMPORTANCE_DEFAULT
    }

    private companion object {
        /** Every agent notification has this id; its tag is the session. */
        const val ID = 107
        const val EXTRA_UPDATE = "cylist.agent.update"
        const val EXTRA_ROLE = "cylist.agent.pillRole"
    }
}
