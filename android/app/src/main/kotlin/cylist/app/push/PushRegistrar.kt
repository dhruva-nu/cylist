package cylist.app.push

import android.content.Context
import androidx.core.content.edit
import cylist.api.generated.apis.DevicesApi
import cylist.api.generated.models.DevicePlatform
import cylist.api.generated.models.DeviceRegister
import cylist.app.session.Session
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.util.UUID

/** Which server-side device this phone registered under each of its tokens. */
interface DeviceIds {
    fun get(tokenId: String): String?

    fun put(tokenId: String, deviceId: String)

    fun remove(tokenId: String)
}

/** [DeviceIds] in a private SharedPreferences file. Ids, not secrets. */
class PreferencesDeviceIds(context: Context) : DeviceIds {
    private val prefs = context.getSharedPreferences("push_devices", Context.MODE_PRIVATE)

    override fun get(tokenId: String): String? = prefs.getString(tokenId, null)

    override fun put(tokenId: String, deviceId: String) = prefs.edit { putString(tokenId, deviceId) }

    override fun remove(tokenId: String) = prefs.edit { remove(tokenId) }
}

/**
 * Tells the signed-in server where this phone's pushes go, and stops it again.
 *
 *  - [register] after every sign-in, at every launch while signed in, and
 *    whenever Firebase rotates the token: `POST /devices` is idempotent.
 *  - [unregister] before signing out, while the token still works:
 *    `DELETE /devices/{id}`. The server also stops pushing to a device whose
 *    token is revoked, so a phone that cannot reach it here is still silenced.
 *
 * Without Firebase ([FcmTokens] answers null) both do nothing.
 */
class PushRegistrar(
    private val tokens: FcmTokens,
    private val ids: DeviceIds,
    /** What the device is called in the server's list: the phone's token name. */
    private val label: (Session) -> String,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) {
    /** Register this phone for [session]. Returns the device id, or null when push is off or it failed. */
    suspend fun register(session: Session, fcmToken: String? = null): String? = withContext(io) {
        val token = fcmToken ?: tokens.current() ?: return@withContext null
        try {
            val device = session.client.api(::DevicesApi).registerDeviceApiV1DevicesPost(
                // Every field given: the generated client would send a null platform.
                DeviceRegister(fcmToken = token, label = label(session).take(LABEL_MAX), platform = DevicePlatform.android),
            )
            ids.put(session.account.tokenId, device.id.toString())
            device.id.toString()
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // Offline, or an older server without /devices: tried again at the next launch.
            null
        }
    }

    /** Forget this phone's device on [session]'s server. Returns whether the server confirmed it. */
    suspend fun unregister(session: Session): Boolean = withContext(io) {
        val tokenId = session.account.tokenId
        val deviceId = ids.get(tokenId) ?: return@withContext false
        ids.remove(tokenId)
        try {
            session.client.api(::DevicesApi).unregisterDeviceApiV1DevicesDeviceIdDelete(UUID.fromString(deviceId))
            true
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            false
        }
    }

    private companion object {
        /** The server's limit on a device label. */
        const val LABEL_MAX = 120
    }
}
