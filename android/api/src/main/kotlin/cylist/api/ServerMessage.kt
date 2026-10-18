package cylist.api

import cylist.api.generated.infrastructure.ClientError
import cylist.api.generated.infrastructure.ClientException
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive

private val json = Json { ignoreUnknownKeys = true }

/**
 * The server's own words for a request it refused, when it gave some.
 *
 * The API answers a 4xx with `{"error": {"code", "message", "details"}}`
 * (`app/errors.py`), and its message is written to be shown to a person
 * ("That to-do moved to Mon 19 Oct"). The generated client keeps that body as
 * a string on the [ClientException]; this reads the message back out of it.
 * Null for anything else, so the caller can fall back to its own wording.
 */
fun serverMessage(error: Throwable): String? {
    val body = ((error as? ClientException)?.response as? ClientError<*>)?.body as? String ?: return null
    val root = runCatching { json.parseToJsonElement(body) }.getOrNull() as? JsonObject ?: return null
    val message = (root["error"] as? JsonObject)?.get("message") as? JsonPrimitive
    return message?.takeIf { it.isString }?.content?.takeIf { it.isNotBlank() }
}
