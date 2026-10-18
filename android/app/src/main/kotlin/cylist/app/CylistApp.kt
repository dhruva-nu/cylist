package cylist.app

import android.app.Application
import android.os.Build
import android.provider.Settings
import cylist.api.Accounts
import cylist.app.session.KeystoreBox
import cylist.app.session.PreferencesSlot
import cylist.app.session.SealedSessionStore
import cylist.app.session.Sessions
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.MainScope

/** The process: the one [Sessions], and what outlives any one screen. */
class CylistApp : Application() {
    val accounts: Accounts by lazy { Accounts() }

    /** Read from the sealed store on first use, which is the first frame of [MainActivity]. */
    val sessions: Sessions by lazy {
        Sessions(
            store = SealedSessionStore(KeystoreBox(), PreferencesSlot(this)),
            accounts = accounts,
            deviceName = deviceName(),
        )
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
