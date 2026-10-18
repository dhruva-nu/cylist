package cylist.api

import cylist.api.generated.apis.AuthApi
import cylist.api.generated.models.Channel
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Before
import org.junit.Test

class CylistClientTest {
    private val server = MockWebServer()

    @Before
    fun start() = server.start()

    @After
    fun stop() = server.close()

    @Test
    fun sendsTheTokenAndReadsTheGeneratedModel() {
        server.enqueue(
            MockResponse.Builder()
                .addHeader("Content-Type", "application/json")
                // `added_later` is not in the snapshot: an older app has to
                // keep working against a newer server.
                .body(
                    """
                    {"token_id": "0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b", "label": "phone",
                     "channel": "api", "scopes": ["read", "write"], "person": null,
                     "added_later": true}
                    """.trimIndent(),
                )
                .build(),
        )

        val me = CylistClient(server.url("/").toString(), "cyl_abc").api(::AuthApi).meApiV1MeGet()

        val request = server.takeRequest()
        assertEquals("/api/v1/me", request.url.encodedPath)
        assertEquals("Bearer cyl_abc", request.headers["Authorization"])
        assertEquals("phone", me.label)
        assertEquals(Channel.api, me.channel)
        assertEquals(listOf("read", "write"), me.scopes)
        assertNull(me.person)
    }

    @Test(expected = IllegalArgumentException::class)
    fun refusesSomethingThatIsNotAToken() {
        CylistClient("https://cylist.in", "hunter2")
    }
}
