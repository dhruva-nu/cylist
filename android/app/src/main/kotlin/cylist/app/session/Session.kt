package cylist.app.session

import cylist.api.CylistClient
import kotlinx.serialization.Serializable
import okhttp3.OkHttpClient

/**
 * What the phone keeps after signing in: the server, and the personal token
 * minted for this device. Never a password. Sealed with a Keystore key before
 * it is written anywhere (see [SealedSessionStore]).
 */
@Serializable
data class Account(
    /** A [cylist.api.ServerAddress]'s url, as it was when this account signed in. */
    val server: String,
    val token: String,
    /** The token's id on the server, which is what signing out revokes. */
    val tokenId: String,
    /** What the token is called in the web app's token list. */
    val tokenName: String,
    val personName: String,
    val personEmail: String? = null,
) {
    /** Never the token: an Account in a log line or a crash report says only who and where. */
    override fun toString(): String = "Account($personName on $server, token \"$tokenName\")"
}

/**
 * Who the app is signed in as, and where: an [Account] and a client for it.
 *
 * Every screen talks to the server through [client]. Get the current session
 * from [Sessions.state] or [Sessions.current]; nothing else builds one. When
 * the server stops accepting the token (an HTTP 401 on any call made through
 * [client]), [Sessions] is told and signs the app out, so it lands on the
 * sign-in screen saying why. A screen holding a socket calls [refused] itself
 * when the socket is refused (a board socket's 4401).
 */
class Session internal constructor(
    val account: Account,
    http: OkHttpClient,
    private val onRefused: (Session) -> Unit,
) {
    /** The server this session belongs to. */
    val server: String get() = account.server

    val client: CylistClient by lazy {
        CylistClient(
            account.server,
            account.token,
            http.newBuilder()
                .addInterceptor { chain ->
                    chain.proceed(chain.request()).also { if (it.code == HTTP_UNAUTHORIZED) onRefused(this) }
                }
                .build(),
        )
    }

    /** Say the server refused this session's token. [Sessions] signs the app out. */
    fun refused() = onRefused(this)

    override fun toString(): String = "Session($account)"

    private companion object {
        const val HTTP_UNAUTHORIZED = 401
    }
}
