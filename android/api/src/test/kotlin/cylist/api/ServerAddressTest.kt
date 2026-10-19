package cylist.api

import cylist.api.ServerAddress.Parsed
import cylist.api.ServerAddress.Problem
import org.junit.Assert.assertEquals
import org.junit.Test

class ServerAddressTest {
    private val debugHosts = setOf("10.0.2.2", "localhost")

    private fun valid(text: String, cleartext: Set<String> = emptySet()): String {
        val parsed = ServerAddress.parse(text, cleartext)
        check(parsed is Parsed.Valid) { "expected $text to parse, got $parsed" }
        return parsed.address.url
    }

    private fun problem(text: String, cleartext: Set<String> = emptySet()): Problem {
        val parsed = ServerAddress.parse(text, cleartext)
        check(parsed is Parsed.Invalid) { "expected $text to be refused, got $parsed" }
        return parsed.problem
    }

    @Test
    fun takesThePresetsAsTheyAre() {
        assertEquals("https://cylist.in", valid("https://cylist.in"))
        assertEquals("https://stage.cylist.in", valid("https://stage.cylist.in"))
        assertEquals("https://dev3.cylist.in", valid("https://dev3.cylist.in"))
    }

    @Test
    fun aBareHostIsHttps() {
        assertEquals("https://cylist.in", valid("cylist.in"))
        assertEquals("https://cylist.in", valid("  cylist.in/  "))
    }

    @Test
    fun lowercasesTheHostAndDropsTheDefaultPort() {
        assertEquals("https://cylist.in", valid("HTTPS://Cylist.IN:443/"))
        assertEquals("https://box.example:8443", valid("https://box.example:8443"))
    }

    @Test
    fun keepsABasePathButNotTheApiPrefix() {
        assertEquals("https://box.example/cylist", valid("https://box.example/cylist/"))
        assertEquals("https://cylist.in", valid("https://cylist.in/api/v1"))
        assertEquals("https://box.example/cylist", valid("https://box.example/cylist/api/v1/"))
    }

    @Test
    fun cleartextOnlyToTheHostsThisBuildAllows() {
        assertEquals(Problem.Cleartext, problem("http://cylist.in"))
        assertEquals(Problem.Cleartext, problem("http://10.0.2.2:8102"))
        assertEquals(Problem.Cleartext, problem("http://cylist.in", debugHosts))
        assertEquals("http://10.0.2.2:8102", valid("http://10.0.2.2:8102", debugHosts))
        assertEquals("http://localhost:8000", valid("http://localhost:8000/", debugHosts))
    }

    @Test
    fun refusesWhatIsNotAServerAddress() {
        assertEquals(Problem.Empty, problem("   "))
        assertEquals(Problem.UnsupportedScheme, problem("ftp://cylist.in"))
        assertEquals(Problem.Malformed, problem("https://"))
        assertEquals(Problem.Malformed, problem("not a url at all"))
        assertEquals(Problem.HasCredentials, problem("https://me:secret@cylist.in"))
        assertEquals(Problem.HasQuery, problem("https://cylist.in/?next=/board"))
        assertEquals(Problem.HasQuery, problem("https://cylist.in/#board"))
    }

    @Test
    fun displaysWithoutTheScheme() {
        assertEquals("dev1.cylist.in", (ServerAddress.parse("dev1.cylist.in") as Parsed.Valid).address.display)
    }

    @Test
    fun aStoredAddressComesBackAsItWas() {
        assertEquals("http://10.0.2.2:8102", ServerAddress.restore("http://10.0.2.2:8102")?.url)
        assertEquals(null, ServerAddress.restore("not a url"))
    }
}
