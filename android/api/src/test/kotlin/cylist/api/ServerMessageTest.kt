package cylist.api

import cylist.api.generated.infrastructure.ClientError
import cylist.api.generated.infrastructure.ClientException
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class ServerMessageTest {
    private fun refused(body: String?) = ClientException("Client error : 422", 422, ClientError<Any>(body = body, statusCode = 422))

    @Test
    fun readsTheMessageTheServerWroteForAPerson() {
        val body = """{"error":{"code":"unprocessable","message":"That to-do moved to Mon 19 Oct.","details":{}}}"""
        assertEquals("That to-do moved to Mon 19 Oct.", serverMessage(refused(body)))
    }

    @Test
    fun anythingElseIsNullSoTheCallerSaysItsOwn() {
        assertNull(serverMessage(refused("""{"detail":[{"msg":"field required"}]}""")))
        assertNull(serverMessage(refused("<html>Bad gateway</html>")))
        assertNull(serverMessage(refused(null)))
        assertNull(serverMessage(IllegalStateException("boom")))
    }
}
