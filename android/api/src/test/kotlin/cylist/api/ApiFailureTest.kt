package cylist.api

import cylist.api.generated.apis.TasksApi
import cylist.api.generated.models.TaskMove
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.io.IOException
import java.util.UUID

class ApiFailureTest {
    private val server = MockWebServer()

    @Before
    fun setUp() = server.start()

    @After
    fun tearDown() = server.close()

    private fun refusal(status: Int, body: String): Throwable {
        server.enqueue(MockResponse.Builder().code(status).body(body).addHeader("Content-Type", "application/json").build())
        val client = CylistClient(server.url("/").toString(), "cyl_test")
        return try {
            client.api(::TasksApi).moveTaskApiV1TasksTaskRefMovePost("ATL-41", TaskMove(columnId = UUID.randomUUID()))
            fail("the call should have been refused")
            error("unreachable")
        } catch (e: Exception) {
            e
        }
    }

    @Test
    fun theServersSentenceComesThroughFromARefusedCall() {
        val failure = ApiFailure.of(
            refusal(
                422,
                """{"error":{"code":"unprocessable","message":"ATL-41 still has 1 unfinished sub-task: Write tests.","details":{}}}""",
            ),
        )
        assertEquals(ApiFailure(422, "unprocessable", "ATL-41 still has 1 unfinished sub-task: Write tests."), failure)
        assertFalse(failure!!.isConflict)
    }

    @Test
    fun aConflictIsSaidToBeOne() {
        val failure = ApiFailure.of(refusal(409, """{"error":{"code":"conflict","message":"Already answered by Aditi K: Grey."}}"""))
        assertTrue(failure!!.isConflict)
        assertEquals("Already answered by Aditi K: Grey.", failure.message)
    }

    @Test
    fun aServerErrorWithoutAnEnvelopeHasOnlyItsStatus() {
        assertEquals(ApiFailure(502, null, null), ApiFailure.of(refusal(502, "<html>Bad gateway</html>")))
    }

    @Test
    fun aBrokenConnectionIsNotARefusal() {
        assertNull(ApiFailure.of(IOException("reset")))
    }
}
