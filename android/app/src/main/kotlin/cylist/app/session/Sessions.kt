package cylist.app.session

import cylist.api.AccountError
import cylist.api.AccountService
import cylist.api.Revocation
import cylist.api.ServerAddress
import cylist.api.generated.models.Scope
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.withContext
import okhttp3.OkHttpClient
import java.util.UUID

/** Whether the app is signed in, and if not, what to tell the person on the sign-in screen. */
sealed interface SessionState {
    /** [notice] says why the app is signed out when it was not the person's own doing, or how signing out went. */
    data class SignedOut(val notice: String? = null) : SessionState

    data class SignedIn(val session: Session) : SessionState
}

/**
 * The app's one signed-in session, and the only way to start or end it.
 *
 *  - [state] / [current]: the session now. The app's root shows sign-in for
 *    [SessionState.SignedOut] and the app for [SessionState.SignedIn].
 *  - [signIn]: email and password to a minted token, stored sealed.
 *  - [signOut]: revoke the token on the server, then forget it here.
 *  - a refused token (401) signs out by itself, through [Session.client] or
 *    [Session.refused], with a notice saying so.
 *
 * One instance for the process, held by [cylist.app.CylistApp].
 */
class Sessions(
    private val store: SessionStore,
    private val accounts: AccountService,
    /** What the token is named for: this phone, as its owner named it. */
    private val deviceName: String,
    private val http: OkHttpClient = OkHttpClient(),
    private val io: CoroutineDispatcher = Dispatchers.IO,
    /**
     * Run before the token is revoked, while the server still accepts it: the
     * phone forgets its push registration here (CYLIST-107). Its failure is
     * swallowed, because signing out must not depend on it; a revoked token
     * stops the pushes anyway.
     */
    private val beforeSignOut: suspend (Session) -> Unit = {},
) {
    private val _state = MutableStateFlow<SessionState>(
        store.read()?.let { SessionState.SignedIn(sessionFor(it)) } ?: SessionState.SignedOut(),
    )
    val state: StateFlow<SessionState> = _state.asStateFlow()

    /** The signed-in session, or null. */
    fun current(): Session? = (_state.value as? SessionState.SignedIn)?.session

    /** The name the next token will be minted under, as it shows in the web app's token list. */
    val tokenName: String get() = "$deviceName (Android)"

    /**
     * Sign in to [server] and keep the minted token. The password is used for
     * this one request and kept nowhere. Throws [AccountError] with a message
     * fit to show.
     */
    suspend fun signIn(server: ServerAddress, email: String, password: String): Session {
        val account = withContext(io) {
            val minted = accounts.signIn(server, email, password, tokenName, PHONE_SCOPES, optional = setOf(Scope.permit))
            Account(
                server = minted.server.url,
                token = minted.token,
                tokenId = minted.tokenId.toString(),
                tokenName = minted.tokenName,
                personName = minted.personName,
                personEmail = minted.personEmail,
            ).also(store::write)
        }
        return sessionFor(account).also { _state.value = SessionState.SignedIn(it) }
    }

    /**
     * Revoke this phone's token on the server and forget it here. The phone is
     * signed out whatever the server says; when the token could not be
     * revoked, the notice on the sign-in screen says so and where to finish
     * the job.
     */
    suspend fun signOut() {
        val session = current() ?: return
        val account = session.account
        val notice = withContext(io) {
            try {
                beforeSignOut(session)
            } catch (e: CancellationException) {
                throw e
            } catch (_: Exception) {
                // Signing out goes on regardless; see beforeSignOut.
            }
            try {
                when (accounts.revoke(parse(account.server), account.token, UUID.fromString(account.tokenId))) {
                    Revocation.Revoked, Revocation.AlreadyGone -> null
                }
            } catch (e: AccountError) {
                "Signed out of this phone. ${e.message} \"${account.tokenName}\" may still be in the web app's token list; revoke it there."
            } finally {
                store.clear()
            }
        }
        _state.compareAndSet(SessionState.SignedIn(session), SessionState.SignedOut(notice))
    }

    /** The server refused [session]'s token. Forget it, unless the app has already moved on. */
    private fun refused(session: Session) {
        val display = parse(session.server).display
        val out = SessionState.SignedOut("$display no longer accepts this phone's sign-in. Sign in again.")
        if (_state.compareAndSet(SessionState.SignedIn(session), out)) store.clear()
    }

    private fun sessionFor(account: Account) = Session(account, http, ::refused)

    private fun parse(server: String): ServerAddress =
        checkNotNull(ServerAddress.restore(server)) { "the stored server is not a URL" }

    companion object {
        /**
         * What a phone's token may do: read and change the board, cards and
         * comments. Not `admin`, so a lost phone cannot mint tokens or open
         * accounts, and not the vault, which no phone screen shows yet. A card
         * that needs more widens this, and signing in again picks it up.
         * `permit` lets the person answer an agent's permission prompt from
         * the Task detail screen (CYLIST-104): the server refuses that to any
         * token without it, which is every token an agent holds. A server
         * older than `permit` refuses the scope, so it is asked for as
         * optional: the phone then signs in without it, and answering a
         * permission waits until the server is upgraded and the phone signs
         * in again.
         */
        val PHONE_SCOPES: List<Scope> = listOf(Scope.read, Scope.write, Scope.permit)
    }
}
