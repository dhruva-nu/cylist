package cylist.api

import cylist.api.generated.infrastructure.ClientError
import cylist.api.generated.infrastructure.ClientException
import cylist.api.generated.infrastructure.ServerError
import cylist.api.generated.infrastructure.ServerException
import kotlinx.serialization.SerializationException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

/**
 * What the server said when it refused a call: the HTTP status, and the
 * `{"error": {"code", "message"}}` envelope every route answers an error with.
 *
 * The generated client throws a [ClientException] or [ServerException] whose
 * own message is "Client error : 422" and nothing more; the sentence the
 * server wrote for a person to read ("ATL-41 still has 2 unfinished
 * sub-tasks: …") is in the response body. This digs it out, so a screen can
 * show the server's words as they are, the way the web app's `ApiError` does.
 */
data class ApiFailure(
    val status: Int,
    /** The envelope's `code`: `conflict`, `unprocessable`, `not_found`… Null if there was no envelope. */
    val code: String?,
    /** The envelope's `message`, written to be shown as it is. Null if there was no envelope. */
    val message: String?,
) {
    /** A 409: the state changed under the caller (a prompt someone else already answered). */
    val isConflict: Boolean get() = status == HTTP_CONFLICT || code == "conflict"

    companion object {
        private const val HTTP_CONFLICT = 409
        private val json = Json { ignoreUnknownKeys = true }

        /** The failure [error] carries, or null if it is not a refusal from the server (a dropped connection, say). */
        fun of(error: Throwable): ApiFailure? = when (error) {
            is ClientException -> fromBody(error.statusCode, (error.response as? ClientError<*>)?.body)
            is ServerException -> fromBody(error.statusCode, (error.response as? ServerError<*>)?.body)
            else -> null
        }

        /** Read an error envelope out of a response body, tolerating anything that is not one. */
        fun fromBody(status: Int, body: Any?): ApiFailure {
            val envelope = (body as? String)?.let { text ->
                try {
                    (json.parseToJsonElement(text) as? JsonObject)?.get("error") as? JsonObject
                } catch (_: SerializationException) {
                    null
                }
            }
            fun field(name: String): String? = (envelope?.get(name) as? JsonPrimitive)?.takeIf { it.isString }?.content
            return ApiFailure(status, field("code"), field("message")?.takeIf { it.isNotBlank() })
        }
    }
}
