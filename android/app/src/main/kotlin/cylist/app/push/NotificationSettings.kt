package cylist.app.push

import android.Manifest
import android.app.NotificationManager
import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.LifecycleResumeEffect
import cylist.app.CylistApp
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.Radii
import cylist.app.theme.TextSizes

/**
 * Ask once for permission to notify (Android 13+), the first time the signed-in
 * app is shown. Saying no is respected; More says how to change it later.
 */
@Composable
fun AskForNotifications() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
    val context = LocalContext.current
    var asked by rememberSaveable { mutableStateOf(false) }
    val launcher = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) {}
    LaunchedEffect(Unit) {
        if (!asked && !notificationsAllowed(context)) {
            asked = true
            launcher.launch(Manifest.permission.POST_NOTIFICATIONS)
        }
    }
}

/** What the phone currently allows, read again whenever the screen comes back. */
private data class NotifyStatus(val notifications: Boolean, val liveUpdates: LiveUpdates, val push: Boolean)

/**
 * Android 16 Live Updates on this phone. They arrived in 36.1: a 36.0 build has
 * the permission and the API but no settings page and never promotes, so it is
 * told apart from one where the person turned them off.
 */
private enum class LiveUpdates { NotThisAndroid, Off, On }

private fun notificationsAllowed(context: Context): Boolean =
    context.getSystemService(NotificationManager::class.java).areNotificationsEnabled() &&
        (
            Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU ||
                context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED
            )

private fun statusOf(context: Context): NotifyStatus = NotifyStatus(
    notifications = notificationsAllowed(context),
    liveUpdates = when {
        Build.VERSION.SDK_INT < Build.VERSION_CODES.BAKLAVA ||
            Build.VERSION.SDK_INT_FULL < Build.VERSION_CODES_FULL.BAKLAVA_1 -> LiveUpdates.NotThisAndroid
        context.getSystemService(NotificationManager::class.java).canPostPromotedNotifications() -> LiveUpdates.On
        else -> LiveUpdates.Off
    },
    push = (context.applicationContext as CylistApp).pushAvailable,
)

/**
 * Open the first of [actions] this phone has a screen for, for this app.
 * Not every Android 16 has every one: the Live Updates page is missing on 36.0,
 * and a phone maker can drop any of them, so the app's notification page is
 * always the last resort.
 */
private fun openSettings(context: Context, vararg actions: String) {
    for (action in listOf(*actions, Settings.ACTION_APP_NOTIFICATION_SETTINGS)) {
        val intent = Intent(action).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        try {
            context.startActivity(intent)
            return
        } catch (_: ActivityNotFoundException) {
            // Try the next one.
        }
    }
}

/**
 * The More tab's "Notifications on this phone": whether agent alerts can reach
 * this phone, and where each switch that stops them lives — the app's
 * notification permission, Android 16's per-app Live Updates, and OxygenOS's
 * own Live Alerts toggle for the camera pill, which no app can open directly.
 */
@Composable
fun NotificationsSection() {
    val context = LocalContext.current
    val colors = CylistTheme.colors
    var refresh by remember { mutableIntStateOf(0) }
    LifecycleResumeEffect(Unit) {
        refresh++
        onPauseOrDispose {}
    }
    val status = remember(refresh) { statusOf(context) }
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { refresh++ }

    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(Modifier.padding(top = 8.dp), verticalAlignment = Alignment.Bottom) {
            Text("Notifications ", color = colors.ink, fontSize = TextSizes.base, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            Text("on this phone", color = colors.ink3, fontSize = TextSizes.md, fontFamily = Figtree)
        }
        Box(Modifier.fillMaxWidth().padding(bottom = 2.dp).heightIn(min = 1.dp).background(colors.line))

        StatusRow(
            on = status.notifications,
            title = if (status.notifications) "Agent alerts can reach you" else "Notifications are off for Cylist",
            detail = "An agent needs you, an agent finished, and the running ones.",
            action = if (status.notifications) null else "Allow notifications",
            onAction = {
                val neverAsked = Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
                    context.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED
                if (neverAsked) {
                    ask.launch(Manifest.permission.POST_NOTIFICATIONS)
                } else {
                    openSettings(context)
                }
            },
        )

        when (status.liveUpdates) {
            LiveUpdates.NotThisAndroid -> StatusRow(
                on = false,
                title = "No Live Updates on this Android",
                detail = "They arrive with Android 16's 36.1 update. Until then a running agent is an ordinary ongoing notification.",
                action = null,
                onAction = {},
            )
            LiveUpdates.Off, LiveUpdates.On -> {
                val live = status.liveUpdates == LiveUpdates.On
                StatusRow(
                    on = live,
                    title = if (live) "Live Updates are on" else "Live Updates are off for Cylist",
                    detail = "A running agent shows as a chip in the status bar, with how long it has been working.",
                    action = if (live) null else "Open Live Updates settings",
                    onAction = {
                        // Always true here (Off and On are 36.1+ only); said again for lint.
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.BAKLAVA) {
                            openSettings(context, Settings.ACTION_APP_NOTIFICATION_PROMOTION_SETTINGS)
                        }
                    },
                )
            }
        }

        // OxygenOS keeps the camera pill behind a switch of its own, per app,
        // that no intent opens: the most this screen can do is say where it is.
        Column(
            Modifier
                .fillMaxWidth()
                .background(colors.paper, RoundedCornerShape(Radii.card))
                .border(1.dp, colors.hold.copy(alpha = 0.45f), RoundedCornerShape(Radii.card))
                .padding(12.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Dot(colors.hold)
                Text(
                    "Live Alerts on a OnePlus",
                    color = colors.ink,
                    fontSize = TextSizes.base,
                    fontWeight = FontWeight.SemiBold,
                    fontFamily = Figtree,
                    modifier = Modifier.padding(start = 8.dp),
                )
            }
            Text(
                "OxygenOS shows another app's Live Updates in the pill by the camera only once you allow it for that app. " +
                    "Open Settings › Notifications & status bar › Live Alerts and turn on Cylist. " +
                    "It is also in Cylist's own notification settings, as “Show Live Updates on Live Alerts”.",
                color = colors.ink2,
                fontSize = TextSizes.md,
                fontFamily = Figtree,
            )
            SettingsButton("Open Cylist's notification settings") {
                openSettings(context)
            }
        }

        if (!status.push) {
            Text(
                "This build was made without a Firebase config, so the server cannot push to this phone " +
                    "and agent alerts will not arrive.",
                color = colors.ink3,
                fontSize = TextSizes.sm,
                fontFamily = Figtree,
            )
        }
    }
}

@Composable
private fun StatusRow(on: Boolean, title: String, detail: String, action: String?, onAction: () -> Unit) {
    val colors = CylistTheme.colors
    Column(Modifier.fillMaxWidth().padding(vertical = 2.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Dot(if (on) colors.ok else colors.hold)
            Text(
                title,
                color = colors.ink,
                fontSize = TextSizes.lg,
                fontWeight = FontWeight.SemiBold,
                fontFamily = Figtree,
                modifier = Modifier.padding(start = 8.dp),
            )
        }
        Text(detail, color = colors.ink3, fontSize = TextSizes.md, fontFamily = Figtree, modifier = Modifier.padding(start = 15.dp))
        if (action != null) {
            Box(Modifier.padding(start = 15.dp)) { SettingsButton(action, onAction) }
        }
    }
}

@Composable
private fun Dot(color: Color) {
    Box(Modifier.size(7.dp).background(color, CircleShape))
}

@Composable
private fun SettingsButton(label: String, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    OutlinedButton(
        onClick = onClick,
        border = BorderStroke(1.dp, colors.line2),
        colors = ButtonDefaults.outlinedButtonColors(contentColor = colors.ink),
        shape = RoundedCornerShape(Radii.control),
        modifier = Modifier.heightIn(min = 44.dp).padding(top = 6.dp),
    ) {
        Text(label, fontFamily = Figtree, fontWeight = FontWeight.SemiBold, fontSize = TextSizes.md)
    }
}
