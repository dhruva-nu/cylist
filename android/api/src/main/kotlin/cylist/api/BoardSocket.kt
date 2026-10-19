package cylist.api

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import kotlin.math.min
import kotlin.math.pow
import kotlin.math.roundToLong

/**
 * What the server may send down a board socket.
 *
 * WebSocket routes are not in the OpenAPI document, so nothing generates these.
 * They mirror the backend's `app/realtime/routes.py`, as the web app's
 * `frontend/src/api/realtime.ts` does; change the three together.
 *
 * None of them carries board data. Each is a doorbell, and the answer to one
 * is to refetch over HTTP like any other time.
 */
sealed interface BoardEvent {
    /** The socket is good. Whatever happened while there was none, happened: refetch. */
    data object Ready : BoardEvent

    /** Something on this project committed. */
    data class Changed(val at: String) : BoardEvent

    /** An agent on one card read, edited, ran or said something. */
    data class AgentFeed(val taskId: String, val at: String) : BoardEvent

    /** Sent just before a refusal closes the socket. */
    data class Error(val code: String, val message: String) : BoardEvent
}

/** The rules of a board socket, kept apart from the socket so they can be tested alone. */
object BoardSocketProtocol {
    /** No credential, or one the server does not accept. Retrying will not find one. */
    const val UNAUTHORIZED = 4401
    const val FORBIDDEN = 4403
    const val NOT_FOUND = 4404

    private const val FIRST_DELAY_MS = 500.0
    private const val MAX_DELAY_MS = 15_000.0
    private const val JITTER = 0.2

    private val json = Json { ignoreUnknownKeys = true }

    /** Where a project's board socket lives, under whatever path the server is served from. */
    fun url(baseUrl: String, projectKey: String): HttpUrl =
        baseUrl.trimEnd('/').toHttpUrl().newBuilder()
            .addPathSegments("api/v1/projects")
            .addPathSegment(projectKey)
            .addPathSegments("board/ws")
            .build()

    /**
     * How long to wait before attempt [attempt], counting from zero: doubling
     * to fifteen seconds, then jittered, so a server coming back up is not met
     * by every phone at the same instant.
     */
    fun backoffDelayMs(attempt: Int, random: () -> Double = Math::random): Long {
        val flat = min(FIRST_DELAY_MS * 2.0.pow(attempt.coerceAtLeast(0)), MAX_DELAY_MS)
        val spread = flat * JITTER
        return (flat - spread + random() * spread * 2).roundToLong()
    }

    /** Whether a close code means "stop trying" rather than "try again". */
    fun isFinal(code: Int): Boolean = code == UNAUTHORIZED

    /** A message off the wire, or null if it is not one this app understands. */
    fun parse(text: String): BoardEvent? {
        val message = runCatching { json.parseToJsonElement(text) }.getOrNull() as? JsonObject ?: return null
        fun field(name: String): String? = (message[name] as? JsonPrimitive)?.takeIf { it.isString }?.content
        return when (field("type")) {
            "ready" -> BoardEvent.Ready
            "board.changed" -> BoardEvent.Changed(field("at").orEmpty())
            // A feed nudge that does not say which card is one nobody can act on.
            "agent.feed" -> field("task_id")?.takeIf { it.isNotEmpty() }?.let { BoardEvent.AgentFeed(it, field("at").orEmpty()) }
            "error" -> BoardEvent.Error(field("code") ?: "unknown", field("message").orEmpty())
            else -> null
        }
    }
}

/**
 * One project's board socket, held open between [start] and [stop] and
 * reopened with backoff whenever it drops.
 *
 * The listener is called on OkHttp's threads, never the main one.
 */
class BoardSocket internal constructor(
    private val http: OkHttpClient,
    private val url: HttpUrl,
    private val listener: Listener,
    private val random: () -> Double = Math::random,
) {
    interface Listener {
        fun onEvent(event: BoardEvent)

        /** True once the server has said `ready`; false whenever there is no socket. */
        fun onConnectedChanged(connected: Boolean)

        /** The server refused the token for good. Nothing reconnects after this. */
        fun onSignedOut()
    }

    private var live = false
    private var socket: WebSocket? = null
    private var retry: ScheduledFuture<*>? = null
    private var attempt = 0

    /** Open the socket. Opening one that is already open does nothing. */
    @Synchronized
    fun start() {
        if (live) return
        live = true
        attempt = 0
        open()
    }

    /** Close the socket and stop reconnecting. */
    @Synchronized
    fun stop() {
        if (!live) return
        live = false
        retry?.cancel(false)
        retry = null
        val current = socket
        socket = null
        current?.close(NORMAL_CLOSURE, null)
        listener.onConnectedChanged(false)
    }

    @Synchronized
    private fun open() {
        if (!live || socket != null) return
        socket = http.newWebSocket(Request.Builder().url(url).build(), Handler())
    }

    @Synchronized
    private fun closed(webSocket: WebSocket, code: Int?) {
        // A socket that has already been replaced or stopped has nothing left to say:
        // its retry would open a second socket beside the live one.
        if (socket !== webSocket) return
        socket = null
        listener.onConnectedChanged(false)
        if (!live) return
        if (code != null && BoardSocketProtocol.isFinal(code)) {
            live = false
            listener.onSignedOut()
            return
        }
        retry = timer.schedule(::open, BoardSocketProtocol.backoffDelayMs(attempt, random), TimeUnit.MILLISECONDS)
        attempt += 1
    }

    private inner class Handler : WebSocketListener() {
        override fun onMessage(webSocket: WebSocket, text: String) {
            val event = BoardSocketProtocol.parse(text) ?: return
            synchronized(this@BoardSocket) {
                if (socket !== webSocket) return
                // Only on `ready`, not on open: a socket that is accepted and
                // then refused at once would otherwise reset the backoff and spin.
                if (event == BoardEvent.Ready) {
                    attempt = 0
                    listener.onConnectedChanged(true)
                }
                listener.onEvent(event)
            }
        }

        override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
            webSocket.close(NORMAL_CLOSURE, null)
            closed(webSocket, code)
        }

        override fun onClosed(webSocket: WebSocket, code: Int, reason: String) = closed(webSocket, code)

        override fun onFailure(webSocket: WebSocket, t: Throwable, response: Response?) = closed(webSocket, null)
    }

    private companion object {
        const val NORMAL_CLOSURE = 1000

        /** One daemon thread for every socket's reconnect timer; it only ever schedules an open. */
        val timer: ScheduledExecutorService = Executors.newSingleThreadScheduledExecutor { runnable ->
            Thread(runnable, "cylist-board-socket").apply { isDaemon = true }
        }
    }
}
