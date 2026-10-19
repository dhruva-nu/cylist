package cylist.app.push

import android.content.Context
import com.google.firebase.FirebaseApp
import com.google.firebase.FirebaseOptions
import com.google.firebase.messaging.FirebaseMessaging
import cylist.app.BuildConfig
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlin.coroutines.resume

/** This install's FCM registration token, or null when there is none to have. */
fun interface FcmTokens {
    suspend fun current(): String?
}

/**
 * Firebase, started by hand from the BuildConfig fields app/build.gradle.kts
 * fills from google-services.json — or not started at all when that build had
 * no config for its application id, in which case push is off and everything
 * else works as before.
 */
object Firebase {
    /** Whether this build was given a Firebase config for its application id. */
    val configured: Boolean
        get() = listOf(
            BuildConfig.FIREBASE_APP_ID,
            BuildConfig.FIREBASE_API_KEY,
            BuildConfig.FIREBASE_PROJECT_ID,
            BuildConfig.FIREBASE_SENDER_ID,
        ).all { it.isNotBlank() }

    /** Start Firebase if this build can. Returns whether it is running. */
    fun start(context: Context): Boolean {
        if (!configured) return false
        if (FirebaseApp.getApps(context).isEmpty()) {
            FirebaseApp.initializeApp(
                context,
                FirebaseOptions.Builder()
                    .setApplicationId(BuildConfig.FIREBASE_APP_ID)
                    .setApiKey(BuildConfig.FIREBASE_API_KEY)
                    .setProjectId(BuildConfig.FIREBASE_PROJECT_ID)
                    .setGcmSenderId(BuildConfig.FIREBASE_SENDER_ID)
                    .build(),
            )
        }
        return true
    }

    /** The token source for a started Firebase, or one that always answers null. */
    fun tokens(running: Boolean): FcmTokens = if (!running) {
        FcmTokens { null }
    } else {
        FcmTokens {
            suspendCancellableCoroutine { continuation ->
                FirebaseMessaging.getInstance().token.addOnCompleteListener { task ->
                    // A failure (no Play services, no network yet) is "no token for now":
                    // the next sign-in or onNewToken tries again.
                    continuation.resume(if (task.isSuccessful) task.result else null)
                }
            }
        }
    }
}
