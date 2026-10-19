package cylist.app

import android.app.Application
import android.os.Build
import android.provider.Settings
import cylist.api.Accounts
import cylist.app.push.AgentNotifier
import cylist.app.push.CardLink
import cylist.app.push.Firebase
import cylist.app.push.PreferencesDeviceIds
import cylist.app.push.PushRegistrar
import cylist.app.session.KeystoreBox
import cylist.app.session.PreferencesSlot
import cylist.app.session.SealedSessionStore
import cylist.app.session.SessionState
import cylist.app.session.Sessions
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.MainScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.launch

/** The process: the one [Sessions], and what outlives any one screen. */
class CylistApp : Application() {
    val accounts: Accounts by lazy { Accounts() }

    /** Read from the sealed store on first use, which is the first frame of [MainActivity]. */
    val sessions: Sessions by lazy {
        Sessions(
            store = SealedSessionStore(KeystoreBox(), PreferencesSlot(this)),
            accounts = accounts,
            deviceName = deviceName(),
            beforeSignOut = { session ->
                push.unregister(session)
                notifier.clear()
            },
        )
    }

    /** Agent notifications (CYLIST-107). */
    val notifier: AgentNotifier by lazy { AgentNotifier(this) }

    /** Whether this build has Firebase, and so whether this phone can be pushed to at all. */
    val pushAvailable: Boolean by lazy { Firebase.start(this) }

    val push: PushRegistrar by lazy {
        PushRegistrar(Firebase.tokens(pushAvailable), PreferencesDeviceIds(this), label = { it.account.tokenName })
    }

    /** A card an agent notification asked to open; the app shell shows its session, then clears it. */
    val openCard = MutableStateFlow<CardLink?>(null)

    override fun onCreate() {
        super.onCreate()
        notifier.ensureChannels()
        // Every sign-in, and every launch while signed in, tells the server
        // where this phone's pushes go. Idempotent on the server.
        scope.launch {
            sessions.state.collect { state ->
                if (state is SessionState.SignedIn && pushAvailable) push.register(state.session)
            }
        }
    }

    /** For work that must finish even if the screen that started it goes, such as signing out. */
    val scope: CoroutineScope = MainScope()

    /** Hosts a custom server URL may name over plain HTTP: the emulator's, in a debug build only. */
    val cleartextHosts: Set<String> = BuildConfig.CLEARTEXT_HOSTS.split(',').filter { it.isNotBlank() }.toSet()

    /** The name the phone's owner gave it (Settings › About), or its model. */
    private fun deviceName(): String =
        Settings.Global.getString(contentResolver, Settings.Global.DEVICE_NAME)?.takeIf { it.isNotBlank() }
            ?: "${Build.MANUFACTURER.replaceFirstChar { it.uppercase() }} ${Build.MODEL}"
}
