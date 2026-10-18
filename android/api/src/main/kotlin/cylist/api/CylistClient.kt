package cylist.api

import okhttp3.Call
import okhttp3.Interceptor
import okhttp3.OkHttpClient
import okhttp3.Response

/**
 * A connection to one Cylist server, signed in with an API token.
 *
 * The API classes under `cylist.api.generated` are generated from openapi.json
 * at build time; this hands each of them the server and the token, so nothing
 * else in the app builds an HTTP request by hand:
 *
 *     val client = CylistClient("https://cylist.in", token)
 *     val me = client.api(::AuthApi).meApiV1MeGet()
 *
 * Every path in the schema already starts with `/api/v1`, so [baseUrl] is the
 * server's origin and nothing more.
 */
class CylistClient(
    baseUrl: String,
    token: String,
    http: OkHttpClient = OkHttpClient(),
) {
    init {
        // The server mints `cyl_…` tokens; anything else is a password or an
        // invite pasted into the wrong box, and would only come back as a 401.
        require(token.startsWith(TOKEN_PREFIX)) { "not a Cylist API token" }
    }

    private val basePath: String = baseUrl.trimEnd('/')

    /** [http] with the token on every request, sharing its pool and dispatcher. */
    val http: OkHttpClient = http.newBuilder().addInterceptor(BearerToken(token)).build()

    /** One of the generated API classes, pointed at this server: `api(::TasksApi)`. */
    fun <T> api(create: (basePath: String, client: Call.Factory) -> T): T = create(basePath, http)

    private class BearerToken(private val token: String) : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response = chain.proceed(
            chain.request().newBuilder().header("Authorization", "Bearer $token").build(),
        )
    }

    private companion object {
        const val TOKEN_PREFIX = "cyl_"
    }
}
