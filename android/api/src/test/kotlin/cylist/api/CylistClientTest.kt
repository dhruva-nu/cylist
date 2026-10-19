package cylist.api

import cylist.api.generated.apis.AuthApi
import cylist.api.generated.models.Channel
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
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

    @Test
    fun patchesATaskWithOnlyTheFieldsGiven() {
        server.enqueue(MockResponse.Builder().addHeader("Content-Type", "application/json").body("{}").build())

        CylistClient(server.url("/").toString(), "cyl_abc")
            .patchTask("HWL-7", buildJsonObject { put("assignee_id", "0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b") })

        val request = server.takeRequest()
        assertEquals("PATCH", request.method)
        assertEquals("/api/v1/tasks/HWL-7", request.url.encodedPath)
        assertEquals("Bearer cyl_abc", request.headers["Authorization"])
        // Nothing but the assignee: a null due date or goal would take it off the card.
        assertEquals("{\"assignee_id\":\"0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b\"}", request.body?.utf8())
    }

    @Test
    fun aRefusedPatchCarriesTheServersWords() {
        server.enqueue(
            MockResponse.Builder().code(422).addHeader("Content-Type", "application/json")
                .body("{\"error\": {\"code\": \"unprocessable\", \"message\": \"Aditi K is not on HWL.\"}}").build(),
        )

        val error = runCatching {
            CylistClient(server.url("/").toString(), "cyl_abc").patchTask("HWL-7", buildJsonObject { put("assignee_id", "x") })
        }.exceptionOrNull()

        assertEquals(ApiFailure(422, "unprocessable", "Aditi K is not on HWL."), error?.let(ApiFailure::of))
    }

    @Test(expected = IllegalArgumentException::class)
    fun refusesSomethingThatIsNotAToken() {
        CylistClient("https://cylist.in", "hunter2")
    }
}
