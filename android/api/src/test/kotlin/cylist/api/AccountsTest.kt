package cylist.api

import cylist.api.generated.models.Scope
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.net.ServerSocket
import java.util.UUID
import java.util.concurrent.TimeUnit

class AccountsTest {
    private val server = MockWebServer()
    private lateinit var address: ServerAddress
    private val accounts = Accounts()
    private val tokenId = UUID.fromString("0199a3c4-5b6e-7f80-9a1b-2c3d4e5f6a7b")

    @Before
    fun start() {
        server.start()
        address = parse(server.url("/").toString())
    }

    @After
    fun stop() = server.close()

    private fun parse(url: String): ServerAddress =
        (ServerAddress.parse(url, setOf(server.hostName)) as ServerAddress.Parsed.Valid).address

    private fun json(body: String, code: Int = 200, vararg headers: Pair<String, String>): MockResponse =
        MockResponse.Builder().code(code).addHeader("Content-Type", "application/json").apply {
            headers.forEach { (name, value) -> addHeader(name, value) }
        }.body(body.trimIndent()).build()

    private val identity = """
        {"token_id": "0199a3c4-0000-7000-8000-000000000001", "label": "Aditi K", "channel": "web",
         "scopes": ["admin", "read", "write"],
         "person": {"id": "0199a3c4-0000-7000-8000-000000000002", "name": "Aditi K", "kind": "team",
                    "title": "", "responsibilities": "", "email": "aditi@example.com", "colour": "#000000",
                    "archived_at": null, "created_at": "2026-10-01T00:00:00Z", "is_agent": false,
                    "has_account": true, "invite_is_pending": false}}
    """

    private val issued = """
        {"id": "$tokenId", "name": "Pixel 9 (Android)", "scopes": ["read", "write"],
         "created_at": "2026-10-19T00:00:00Z", "expires_at": null, "last_used_at": null,
         "person_id": "0199a3c4-0000-7000-8000-000000000002", "token": "cyl_minted"}
    """

    @Test
    fun signsInMintsATokenAndLogsTheSessionOut() {
        server.enqueue(json(identity, 200, "Set-Cookie" to "cylist_session=cyl_session; Path=/; HttpOnly"))
        server.enqueue(json(issued, 201))
        server.enqueue(json("""{"ok": true}"""))

        val minted = accounts.signIn(address, " aditi@example.com ", "hunter2", "Pixel 9 (Android)", listOf(Scope.read, Scope.write))

        assertEquals("cyl_minted", minted.token)
        assertEquals(tokenId, minted.tokenId)
        assertEquals("Aditi K", minted.personName)
        assertEquals("aditi@example.com", minted.personEmail)

        val login = server.takeRequest()
        assertEquals("/api/v1/auth/login", login.url.encodedPath)
        val loginBody = login.body!!.utf8()
        assertTrue(loginBody, "\"email\":\"aditi@example.com\"" in loginBody)

        val mint = server.takeRequest()
        assertEquals("/api/v1/tokens", mint.url.encodedPath)
        assertEquals("cylist_session=cyl_session", mint.headers["Cookie"])
        val mintBody = mint.body!!.utf8()
        assertTrue(mintBody, "\"name\":\"Pixel 9 (Android)\"" in mintBody)
        assertTrue(mintBody, "\"scopes\":[\"read\",\"write\"]" in mintBody)

        val logout = server.takeRequest()
        assertEquals("/api/v1/auth/logout", logout.url.encodedPath)
        assertEquals("cylist_session=cyl_session", logout.headers["Cookie"])
    }

    @Test
    fun aLongDeviceNameIsCutToWhatTheServerTakes() {
        server.enqueue(json(identity, 200, "Set-Cookie" to "cylist_session=cyl_session; Path=/"))
        server.enqueue(json(issued, 201))
        server.enqueue(json("""{"ok": true}"""))

        accounts.signIn(address, "aditi@example.com", "pw", "x".repeat(300), listOf(Scope.read))

        server.takeRequest()
        val body = server.takeRequest().body!!.utf8()
        assertTrue("\"name\":\"${"x".repeat(Accounts.MAX_TOKEN_NAME)}\"" in body)
    }

    @Test(expected = AccountError.WrongPassword::class)
    fun aWrongPasswordIsSaidSoAndMintsNothing() {
        server.enqueue(json("""{"error": {"code": "unauthorized", "message": "nope"}}""", 401))
        try {
            accounts.signIn(address, "aditi@example.com", "wrong", "phone", listOf(Scope.read))
        } finally {
            assertEquals(1, server.requestCount)
        }
    }

    @Test
    fun theSessionIsLoggedOutEvenWhenMintingFails() {
        server.enqueue(json(identity, 200, "Set-Cookie" to "cylist_session=cyl_session; Path=/"))
        server.enqueue(json("""{"error": {}}""", 500))
        server.enqueue(json("""{"ok": true}"""))

        val error = runCatching {
            accounts.signIn(address, "aditi@example.com", "pw", "phone", listOf(Scope.read))
        }.exceptionOrNull()

        assertTrue(error.toString(), error is AccountError.ServerTrouble)
        server.takeRequest()
        server.takeRequest()
        assertEquals("/api/v1/auth/logout", server.takeRequest().url.encodedPath)
    }

    @Test
    fun aServerOlderThanAnOptionalScopeMintsWithoutIt() {
        server.enqueue(json(identity, 200, "Set-Cookie" to "cylist_session=cyl_session; Path=/"))
        server.enqueue(json("""{"detail": [{"loc": ["body", "scopes", 2], "msg": "Input should be 'read'"}]}""", 422))
        server.enqueue(json(issued, 201))
        server.enqueue(json("""{"ok": true}"""))

        val minted = accounts.signIn(
            address, "aditi@example.com", "pw", "phone",
            listOf(Scope.read, Scope.write, Scope.permit), optional = setOf(Scope.permit),
        )

        assertEquals("cyl_minted", minted.token)
        server.takeRequest()
        assertTrue("\"permit\"" in server.takeRequest().body!!.utf8())
        val retry = server.takeRequest().body!!.utf8()
        assertTrue(retry, "\"scopes\":[\"read\",\"write\"]" in retry)
        assertEquals("/api/v1/auth/logout", server.takeRequest().url.encodedPath)
    }

    @Test
    fun aRefusedTokenIsNotCalledANonCylistServer() {
        server.enqueue(json(identity, 200, "Set-Cookie" to "cylist_session=cyl_session; Path=/"))
        server.enqueue(json("""{"detail": []}""", 422))
        server.enqueue(json("""{"ok": true}"""))

        val error = runCatching {
            accounts.signIn(address, "aditi@example.com", "pw", "phone", listOf(Scope.read, Scope.write))
        }.exceptionOrNull()

        assertTrue(error.toString(), error is AccountError.TokenRefused)
        server.takeRequest()
        server.takeRequest()
        assertEquals("/api/v1/auth/logout", server.takeRequest().url.encodedPath)
    }

    @Test
    fun aWebPageIsNotACylistServer() {
        server.enqueue(MockResponse.Builder().code(200).addHeader("Content-Type", "text/html").body("<html></html>").build())

        val error = runCatching { accounts.probe(address) }.exceptionOrNull()

        assertTrue(error.toString(), error is AccountError.NotCylist)
    }

    @Test
    fun aMissingApiIsNotACylistServer() {
        server.enqueue(MockResponse.Builder().code(404).body("Not Found").build())

        assertTrue(runCatching { accounts.probe(address) }.exceptionOrNull() is AccountError.NotCylist)
    }

    @Test
    fun aServerWithNoAccountsSaysToSetItUpFirst() {
        server.enqueue(json("""{"urls": [], "environment": "dev", "agent_scopes": ["read"], "has_accounts": false}"""))

        assertTrue(runCatching { accounts.probe(address) }.exceptionOrNull() is AccountError.NoAccountsYet)
    }

    @Test
    fun aServerReadyForSignInPassesTheProbe() {
        server.enqueue(json("""{"urls": [], "environment": "dev", "agent_scopes": ["read"], "has_accounts": true}"""))

        accounts.probe(address)

        assertEquals("/api/v1/setup", server.takeRequest().url.encodedPath)
    }

    @Test
    fun nobodyAnsweringIsUnreachableAndSaidQuickly() {
        val closedPort = ServerSocket(0).use { it.localPort }
        val nowhere = parse("http://${server.hostName}:$closedPort")

        val started = System.nanoTime()
        val error = runCatching { accounts.probe(nowhere) }.exceptionOrNull()

        assertTrue(error.toString(), error is AccountError.Unreachable)
        assertTrue(TimeUnit.NANOSECONDS.toSeconds(System.nanoTime() - started) < 6)
        assertTrue(error!!.message!!.startsWith("Can't reach"))
    }

    @Test
    fun aServerThatNeverAnswersTimesOut() {
        val slow = Accounts(OkHttpClient.Builder().readTimeout(200, TimeUnit.MILLISECONDS).build())
        server.enqueue(MockResponse.Builder().headersDelay(5, TimeUnit.SECONDS).body("{}").build())

        assertTrue(runCatching { slow.probe(address) }.exceptionOrNull() is AccountError.Unreachable)
    }

    @Test
    fun revokingSendsTheTokenAgainstItsOwnId() {
        server.enqueue(json("""{"ok": true}"""))

        assertEquals(Revocation.Revoked, accounts.revoke(address, "cyl_minted", tokenId))

        val request = server.takeRequest()
        assertEquals("DELETE", request.method)
        assertEquals("/api/v1/tokens/$tokenId", request.url.encodedPath)
        assertEquals("Bearer cyl_minted", request.headers["Authorization"])
    }

    @Test
    fun aTokenTheServerNoLongerTakesIsAlreadyGone() {
        server.enqueue(json("""{"error": {}}""", 401))
        assertEquals(Revocation.AlreadyGone, accounts.revoke(address, "cyl_minted", tokenId))

        server.enqueue(json("""{"error": {}}""", 404))
        assertEquals(Revocation.AlreadyGone, accounts.revoke(address, "cyl_minted", tokenId))
    }

    @Test
    fun anOlderServerRefusingSelfRevocationIsSaidSo() {
        server.enqueue(json("""{"error": {"details": {"missing_scopes": ["admin"]}}}""", 403))

        val error = runCatching { accounts.revoke(address, "cyl_minted", tokenId) }.exceptionOrNull()

        assertTrue(error.toString(), error is AccountError.RevokeRefused)
    }
}
