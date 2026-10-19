package cylist.app.push

import cylist.app.session.Session
import cylist.app.session.anAccount
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class MemoryDeviceIds : DeviceIds {
    val ids = mutableMapOf<String, String>()

    override fun get(tokenId: String): String? = ids[tokenId]

    override fun put(tokenId: String, deviceId: String) {
        ids[tokenId] = deviceId
    }

    override fun remove(tokenId: String) {
        ids.remove(tokenId)
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class PushRegistrarTest {
    private val server = MockWebServer()
    private val ids = MemoryDeviceIds()
    private lateinit var session: Session

    @Before
    fun start() {
        server.start()
        session = Session(anAccount.copy(server = server.url("/").toString().trimEnd('/')), OkHttpClient()) {}
    }

    @After
    fun stop() = server.close()

    private fun json(code: Int, body: String) =
        MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").body(body).build()

    private fun device(id: String) = """
        {"id": "$id", "platform": "android", "label": "Pixel 9 (Android)",
         "person_id": "0199f1a2-0000-7000-8000-0000000000aa", "token_id": "${anAccount.tokenId}",
         "created_at": "2026-10-19T09:00:00Z", "last_registered_at": "2026-10-19T09:00:00Z"}
    """.trimIndent()

    @Test
    fun registersTheFcmTokenUnderThePhoneSTokenName() = runTest {
        server.enqueue(json(200, device(DEVICE)))
        val registrar = PushRegistrar({ "fcm-token-1" }, ids, { it.account.tokenName }, UnconfinedTestDispatcher(testScheduler))

        val id = registrar.register(session)

        assertEquals(DEVICE, id)
        assertEquals(DEVICE, ids.get(anAccount.tokenId))
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("/api/v1/devices", request.target)
        assertEquals("Bearer cyl_phone", request.headers["Authorization"])
        val body = request.body!!.utf8()
        assertTrue(body, body.contains("\"fcm_token\":\"fcm-token-1\""))
        assertTrue(body, body.contains("\"label\":\"Pixel 9 (Android)\""))
        assertTrue(body, body.contains("\"platform\":\"android\""))
    }

    @Test
    fun aRotatedTokenIsRegisteredAsGiven() = runTest {
        server.enqueue(json(200, device(DEVICE)))
        val registrar = PushRegistrar({ "stale" }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        registrar.register(session, fcmToken = "fresh")

        assertTrue(server.takeRequest().body!!.utf8().contains("\"fcm_token\":\"fresh\""))
    }

    @Test
    fun withoutFirebaseNothingIsSent() = runTest {
        val registrar = PushRegistrar({ null }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        assertNull(registrar.register(session))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun aServerWithoutDevicesIsNotAnError() = runTest {
        server.enqueue(json(404, """{"detail": "Not Found"}"""))
        val registrar = PushRegistrar({ "t" }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        assertNull(registrar.register(session))
        assertTrue(ids.ids.isEmpty())
    }

    @Test
    fun unregisteringDeletesTheDeviceItRegistered() = runTest {
        ids.put(anAccount.tokenId, DEVICE)
        server.enqueue(json(200, """{"ok": true}"""))
        val registrar = PushRegistrar({ "t" }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        assertTrue(registrar.unregister(session))

        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/api/v1/devices/$DEVICE", request.target)
        assertNull(ids.get(anAccount.tokenId))
    }

    @Test
    fun unregisteringWithNothingRegisteredSendsNothing() = runTest {
        val registrar = PushRegistrar({ "t" }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        assertFalse(registrar.unregister(session))
        assertEquals(0, server.requestCount)
    }

    @Test
    fun anUnreachableServerStillForgetsTheIdLocally() = runTest {
        ids.put(anAccount.tokenId, DEVICE)
        server.enqueue(json(500, "{}"))
        val registrar = PushRegistrar({ "t" }, ids, { "x" }, UnconfinedTestDispatcher(testScheduler))

        assertFalse(registrar.unregister(session))
        assertNull(ids.get(anAccount.tokenId))
    }

    private companion object {
        const val DEVICE = "0199f1a2-0000-7000-8000-0000000000dd"
    }
}
