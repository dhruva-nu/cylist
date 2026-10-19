package cylist.api

import cylist.api.generated.infrastructure.ClientError
import cylist.api.generated.infrastructure.ClientException
import cylist.api.generated.infrastructure.ServerError
import cylist.api.generated.infrastructure.ServerException
import kotlinx.serialization.json.JsonObject
import okhttp3.Call
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.Interceptor
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
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

    /**
     * A project's board socket, carrying the same token. Not opened until
     * [BoardSocket.start]; see [BoardSocket] for what it sends.
     */
    fun boardSocket(projectKey: String, listener: BoardSocket.Listener): BoardSocket =
        BoardSocket(http, BoardSocketProtocol.url(basePath, projectKey), listener)

    /**
     * `PATCH /tasks/{ref}` with only [fields]: a partial edit of one card.
     *
     * The generated `updateTaskApiV1TasksTaskRefPatch` cannot make one. The
     * generated client encodes every default, so `TaskUpdate(assigneeId = …)`
     * also sends `"due_date": null`, `"goal_id": null`, `"template_id": null`
     * and `"jira_ref": null`, and the server reads a null in those four as
     * taking it off the card (`services/tasks/editing.py`, `_CLEARABLE`).
     *
     * A refusal throws the generated client's [ClientException] or
     * [ServerException], so [ApiFailure.of] reads it as it does any other call's.
     */
    fun patchTask(ref: String, fields: JsonObject) {
        val url = "$basePath/api/v1/tasks".toHttpUrl().newBuilder().addPathSegment(ref).build()
        val request = Request.Builder().url(url).patch(fields.toString().toRequestBody(JSON)).build()
        http.newCall(request).execute().use { response ->
            if (response.isSuccessful) return
            val status = response.code
            val body = response.body.string()
            val headers = response.headers.toMultimap()
            throw if (status < HTTP_SERVER_ERROR) {
                ClientException("Client error : $status", status, ClientError<Unit>(response.message, body, status, headers))
            } else {
                ServerException("Server error : $status", status, ServerError<Unit>(response.message, body, status, headers))
            }
        }
    }

    private class BearerToken(private val token: String) : Interceptor {
        override fun intercept(chain: Interceptor.Chain): Response = chain.proceed(
            chain.request().newBuilder().header("Authorization", "Bearer $token").build(),
        )
    }

    private companion object {
        const val TOKEN_PREFIX = "cyl_"
        const val HTTP_SERVER_ERROR = 500
        val JSON = "application/json".toMediaType()
    }
}
