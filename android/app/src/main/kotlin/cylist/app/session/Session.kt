package cylist.app.session

import cylist.api.CylistClient
import cylist.app.BuildConfig

/**
 * Who the app is signed in as, and where.
 *
 * Sign-in, the server picker and Keystore-backed storage are CYLIST-102. Until
 * then the only session is a debug build's, read from android/local.properties
 * at build time (see app/build.gradle.kts); a release build has none.
 */
data class Session(val server: String, val token: String) {
    val client: CylistClient by lazy { CylistClient(server, token) }

    companion object {
        fun current(): Session? {
            val server = BuildConfig.DEV_SERVER
            val token = BuildConfig.DEV_TOKEN
            return if (server.isBlank() || token.isBlank()) null else Session(server, token)
        }
    }
}
