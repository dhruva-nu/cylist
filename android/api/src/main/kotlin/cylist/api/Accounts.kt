package cylist.api

import cylist.api.generated.apis.AuthApi
import cylist.api.generated.apis.SetupApi
import cylist.api.generated.apis.TokensApi
import cylist.api.generated.infrastructure.ClientException
import cylist.api.generated.infrastructure.ServerException
import cylist.api.generated.models.LoginRequest
import cylist.api.generated.models.Scope
import cylist.api.generated.models.TokenCreate
import kotlinx.serialization.SerializationException
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl
import okhttp3.OkHttpClient
import java.io.IOException
import java.util.UUID
import java.util.concurrent.TimeUnit
import javax.net.ssl.SSLException

/** A personal token minted for this device by [Accounts.signIn]. Its plaintext exists nowhere else. */
data class MintedToken(
    val server: ServerAddress,
    val token: String,
    val tokenId: UUID,
    val tokenName: String,
    val personName: String,
    val personEmail: String?,
)

/**
 * Why signing in or out did not work. Every message is written to be shown
 * to the person as it is, and none of them leaks which half of a sign-in was
 * wrong: the server does not say, so neither does the app.
 */
sealed class AccountError(message: String, cause: Throwable? = null) : Exception(message, cause) {
    /** Nothing answered: no route, no DNS, refused, or too slow. */
    class Unreachable(server: ServerAddress, cause: Throwable) :
        AccountError("Can't reach ${server.display}. Check the address and your connection.", cause)

    /** Something answered, over TLS that could not be trusted. */
    class Insecure(server: ServerAddress, cause: Throwable) :
        AccountError("Couldn't make a secure connection to ${server.display}.", cause)

    /** Something answered, and it is not a Cylist API. */
    class NotCylist(server: ServerAddress, cause: Throwable? = null) :
        AccountError("${server.display} answered, but it is not a Cylist server.", cause)

    /** A server with nobody on it yet, whose first sign-in is the bootstrap password in a browser. */
    class NoAccountsYet(server: ServerAddress) :
        AccountError("${server.display} has no accounts yet. Open it in a browser to set it up first.")

    class WrongPassword(cause: Throwable? = null) :
        AccountError("That email and password do not match an account.", cause)

    /** The server would not let this token revoke itself: one older than self-revocation. */
    class RevokeRefused(cause: Throwable? = null) :
        AccountError("The server would not revoke this phone's token. Revoke it from the web app's token list.", cause)

    /** The server failed. */
    class ServerTrouble(server: ServerAddress, status: Int) :
        AccountError("${server.display} had a problem (HTTP $status). Try again in a moment.")
}

/** What became of a token on the server when the phone signed out. */
enum class Revocation {
    /** The server revoked it just now. */
    Revoked,

    /** It was not live to begin with: revoked on the web, expired, or its person archived. */
    AlreadyGone,
}

/** Signing in and out, as the app sees it; [Accounts] is the real one. Every call blocks. */
interface AccountService {
    /** Check that [server] is a Cylist server somebody can sign in to, or throw [AccountError]. */
    fun probe(server: ServerAddress)

    /** Sign in as [email] and mint a token called [tokenName] carrying [scopes], or throw [AccountError]. */
    fun signIn(server: ServerAddress, email: String, password: String, tokenName: String, scopes: List<Scope>): MintedToken

    /** Revoke [token], whose id is [tokenId], using the token itself, or throw [AccountError]. */
    fun revoke(server: ServerAddress, token: String, tokenId: UUID): Revocation
}

/**
 * Signing this device in and out of a Cylist server, through the generated
 * client and nothing hand-built.
 *
 * Signing in is the web app's own login, used once: email and password buy a
 * browser session (`POST /auth/login`), which mints a personal `cyl_…` token
 * named for the device (`POST /tokens`), and is then logged out at once
 * (`POST /auth/logout`) so no month-long cookie is left behind. The phone
 * keeps the token, never the password. Signing out is the token revoking
 * itself (`DELETE /tokens/{id}`), which any credential may do.
 *
 * Every call blocks; run them off the main thread. Timeouts are short so an
 * unreachable server is reported in seconds instead of spinning.
 */
class Accounts(private val http: OkHttpClient = defaultHttp()) : AccountService {

    override fun probe(server: ServerAddress) {
        val setup = call(server) { SetupApi(server.url, http).setupApiV1SetupGet() }
        if (!setup.hasAccounts) throw AccountError.NoAccountsYet(server)
    }

    override fun signIn(
        server: ServerAddress,
        email: String,
        password: String,
        tokenName: String,
        scopes: List<Scope>,
    ): MintedToken {
        // One cookie jar per sign-in, dropped with it: the session cookie is a
        // means to the token and lives only in this call.
        val browser = http.newBuilder().cookieJar(OneServerCookies()).build()
        val identity = call(server) {
            AuthApi(server.url, browser).loginApiV1AuthLoginPost(LoginRequest(password = password, email = email.trim()))
        }
        try {
            val issued = call(server) {
                TokensApi(server.url, browser).createTokenApiV1TokensPost(
                    TokenCreate(name = tokenName.take(MAX_TOKEN_NAME), scopes = scopes),
                )
            }
            return MintedToken(
                server = server,
                token = issued.token,
                tokenId = issued.id,
                tokenName = issued.name,
                personName = identity.person?.name ?: identity.label,
                personEmail = identity.person?.email,
            )
        } finally {
            // Best effort: if the server cannot be told, the session still
            // expires by itself, and the token is what matters.
            runCatching { AuthApi(server.url, browser).logoutApiV1AuthLogoutPost() }
        }
    }

    override fun revoke(server: ServerAddress, token: String, tokenId: UUID): Revocation {
        val client = CylistClient(server.url, token, http)
        return try {
            call(server, forbidden = AccountError::RevokeRefused) {
                client.api(::TokensApi).revokeTokenApiV1TokensTokenIdDelete(tokenId)
            }
            Revocation.Revoked
        } catch (e: AccountError) {
            // 401: the server no longer accepts this token. 404: it never had
            // it. Either way it is not live, which is the outcome wanted.
            when ((e.cause as? ClientException)?.statusCode) {
                HTTP_UNAUTHORIZED, HTTP_NOT_FOUND -> Revocation.AlreadyGone
                else -> throw e
            }
        }
    }

    /**
     * Run one generated call, turning whatever it throws into an [AccountError].
     * [forbidden] says what a 403 means for this call.
     */
    private fun <T> call(
        server: ServerAddress,
        forbidden: (ClientException) -> AccountError = { AccountError.NotCylist(server, it) },
        block: () -> T,
    ): T = try {
        block()
    } catch (e: ClientException) {
        throw when (e.statusCode) {
            HTTP_UNAUTHORIZED -> AccountError.WrongPassword(e)
            HTTP_FORBIDDEN -> forbidden(e)
            else -> AccountError.NotCylist(server, e)
        }
    } catch (e: ServerException) {
        throw AccountError.ServerTrouble(server, e.statusCode)
    } catch (e: SSLException) {
        throw AccountError.Insecure(server, e)
    } catch (e: IOException) {
        throw AccountError.Unreachable(server, e)
    } catch (e: SerializationException) {
        // A 200 that is not the JSON asked for: a web page, a captive portal.
        throw AccountError.NotCylist(server, e)
    } catch (e: IllegalArgumentException) {
        // kotlinx.serialization reports a missing field this way.
        throw AccountError.NotCylist(server, e)
    } catch (e: UnsupportedOperationException) {
        // The generated client does not follow a redirect it is handed.
        throw AccountError.NotCylist(server, e)
    }

    /** Holds the session cookie for the length of one sign-in. */
    private class OneServerCookies : CookieJar {
        private val cookies = mutableListOf<Cookie>()

        @Synchronized
        override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
            this.cookies.removeAll { old -> cookies.any { it.name == old.name } }
            this.cookies += cookies
        }

        @Synchronized
        override fun loadForRequest(url: HttpUrl): List<Cookie> = cookies.filter { it.matches(url) }
    }

    companion object {
        /** `TokenCreate.name`'s limit on the server. */
        const val MAX_TOKEN_NAME = 120

        private const val HTTP_UNAUTHORIZED = 401
        private const val HTTP_FORBIDDEN = 403
        private const val HTTP_NOT_FOUND = 404

        /**
         * Short enough that an address nobody answers at is reported within
         * about five seconds, long enough for a phone on a slow network.
         */
        fun defaultHttp(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(5, TimeUnit.SECONDS)
            .readTimeout(10, TimeUnit.SECONDS)
            .writeTimeout(10, TimeUnit.SECONDS)
            .callTimeout(20, TimeUnit.SECONDS)
            .build()
    }
}
