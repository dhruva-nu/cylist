package cylist.api

import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.Response
import okhttp3.WebSocket
import okhttp3.WebSocketListener
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class BoardSocketTest {
    private val server = MockWebServer()

    @Before
    fun start() = server.start()

    @After
    fun stop() = server.close()

    @Test
    fun parsesEveryDoorbellAndIgnoresTheRest() {
        assertEquals(BoardEvent.Ready, BoardSocketProtocol.parse("""{"type":"ready"}"""))
        assertEquals(BoardEvent.Changed("2026-10-19T10:00:00Z"), BoardSocketProtocol.parse("""{"type":"board.changed","at":"2026-10-19T10:00:00Z"}"""))
        assertEquals(BoardEvent.AgentFeed("t1", ""), BoardSocketProtocol.parse("""{"type":"agent.feed","task_id":"t1"}"""))
        assertEquals(BoardEvent.Error("unauthorized", "no"), BoardSocketProtocol.parse("""{"type":"error","code":"unauthorized","message":"no"}"""))
        assertNull(BoardSocketProtocol.parse("""{"type":"agent.feed"}"""))
        assertNull(BoardSocketProtocol.parse("""{"type":"something.new"}"""))
        assertNull(BoardSocketProtocol.parse("not json"))
        assertNull(BoardSocketProtocol.parse("[1]"))
    }

    @Test
    fun theUrlKeepsTheServersBasePath() {
        assertEquals(
            "https://cylist.in/dev_2/api/v1/projects/ATL/board/ws",
            BoardSocketProtocol.url("https://cylist.in/dev_2/", "ATL").toString(),
        )
    }

    @Test
    fun backoffDoublesToACeilingAndIsJittered() {
        assertEquals(500L, BoardSocketProtocol.backoffDelayMs(0) { 0.5 })
        assertEquals(1000L, BoardSocketProtocol.backoffDelayMs(1) { 0.5 })
        assertEquals(15_000L, BoardSocketProtocol.backoffDelayMs(10) { 0.5 })
        assertEquals(400L, BoardSocketProtocol.backoffDelayMs(0) { 0.0 })
        assertEquals(600L, BoardSocketProtocol.backoffDelayMs(0) { 1.0 })
    }

    @Test
    fun sendsTheTokenAndPassesDoorbellsOn() {
        server.enqueue(upgrade { it.send("""{"type":"ready"}"""); it.send("""{"type":"board.changed","at":"now"}""") })
        val heard = Recorder()
        val socket = CylistClient(server.url("/").toString(), "cyl_abc").boardSocket("ATL", heard)

        socket.start()
        assertEquals(true, heard.connected.poll(5, TimeUnit.SECONDS))
        assertEquals(BoardEvent.Ready, heard.events.poll(5, TimeUnit.SECONDS))
        assertEquals(BoardEvent.Changed("now"), heard.events.poll(5, TimeUnit.SECONDS))

        val handshake = server.takeRequest()
        assertEquals("/api/v1/projects/ATL/board/ws", handshake.url.encodedPath)
        assertEquals("Bearer cyl_abc", handshake.headers["Authorization"])

        socket.stop()
        assertEquals(false, heard.connected.poll(5, TimeUnit.SECONDS))
    }

    @Test
    fun reconnectsAfterADropAndResyncsOnReady() {
        server.enqueue(upgrade { it.close(1011, "restarting") })
        server.enqueue(upgrade { it.send("""{"type":"ready"}""") })
        val heard = Recorder()
        val socket = CylistClient(server.url("/").toString(), "cyl_abc").boardSocket("ATL", heard)

        socket.start()
        assertEquals(BoardEvent.Ready, heard.events.poll(5, TimeUnit.SECONDS))
        assertEquals(2, server.requestCount)
        socket.stop()
    }

    @Test
    fun aRefusedTokenStopsReconnecting() {
        server.enqueue(
            upgrade {
                it.send("""{"type":"error","code":"unauthorized","message":"Signed out"}""")
                it.close(BoardSocketProtocol.UNAUTHORIZED, "")
            },
        )
        val heard = Recorder()
        val socket = CylistClient(server.url("/").toString(), "cyl_abc").boardSocket("ATL", heard)

        socket.start()
        assertTrue(heard.signedOut.poll(5, TimeUnit.SECONDS) == true)
        Thread.sleep(1_000)
        assertEquals(1, server.requestCount)
        socket.stop()
    }

    /** A handshake the server accepts, then acts on as [onOpen] says. */
    private fun upgrade(onOpen: (WebSocket) -> Unit): MockResponse = MockResponse.Builder()
        .webSocketUpgrade(
            object : WebSocketListener() {
                override fun onOpen(webSocket: WebSocket, response: Response) = onOpen(webSocket)

                // Answer the app's close, or the server waits on it forever.
                override fun onClosing(webSocket: WebSocket, code: Int, reason: String) {
                    webSocket.close(code, null)
                }
            },
        )
        .build()

    private class Recorder : BoardSocket.Listener {
        val events = LinkedBlockingQueue<BoardEvent>()
        val connected = LinkedBlockingQueue<Boolean>()
        val signedOut = LinkedBlockingQueue<Boolean>()

        override fun onEvent(event: BoardEvent) {
            events.add(event)
        }

        override fun onConnectedChanged(connected: Boolean) {
            this.connected.add(connected)
        }

        override fun onSignedOut() {
            signedOut.add(true)
        }
    }
}
