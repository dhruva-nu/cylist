package cylist.app.session

import cylist.api.AccountError
import cylist.api.Revocation
import cylist.api.generated.apis.AuthApi
import cylist.api.generated.models.Scope
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.runTest
import mockwebserver3.MockResponse
import mockwebserver3.MockWebServer
import okhttp3.OkHttpClient
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class SessionsTest {
    private val store = MemoryStore()
    private val accounts = FakeAccounts()

    private fun sessions(io: CoroutineDispatcher) =
        Sessions(store, accounts, deviceName = "Pixel 9", http = OkHttpClient(), io = io)

    @Test
    fun startsSignedOutWithNothingStored() = runTest {
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        assertEquals(SessionState.SignedOut(), sessions.state.value)
        assertNull(sessions.current())
    }

    @Test
    fun aStoredAccountIsSignedInAtLaunch() = runTest {
        store.account = anAccount

        val session = sessions(UnconfinedTestDispatcher(testScheduler)).current()

        assertEquals(anAccount, session?.account)
    }

    @Test
    fun signingInMintsANarrowTokenNamedForThePhoneAndStoresIt() = runTest {
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        val session = sessions.signIn(address("https://cylist.in"), "aditi@example.com", "hunter2")

        assertEquals(listOf(Triple("aditi@example.com", "Pixel 9 (Android)", listOf(Scope.read, Scope.write, Scope.permit))), accounts.signIns)
        assertEquals(listOf(setOf(Scope.permit)), accounts.optionalScopes)
        assertSame(session, sessions.current())
        val stored = store.account!!
        assertEquals("cyl_phone", stored.token)
        assertEquals(FakeAccounts.TOKEN_ID.toString(), stored.tokenId)
        assertEquals("https://cylist.in", stored.server)
        assertTrue("the password is kept nowhere", "hunter2" !in stored.toString() && "hunter2" !in listOf(stored.token, stored.tokenName))
    }

    @Test
    fun aFailedSignInStoresNothing() = runTest {
        accounts.signInFails = AccountError.WrongPassword()
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        val error = runCatching { sessions.signIn(address("https://cylist.in"), "a@b.c", "nope") }.exceptionOrNull()

        assertTrue(error is AccountError.WrongPassword)
        assertNull(store.account)
        assertEquals(SessionState.SignedOut(), sessions.state.value)
    }

    @Test
    fun signingOutRevokesTheTokenAndForgetsIt() = runTest {
        store.account = anAccount
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        sessions.signOut()

        assertEquals(listOf("cyl_phone" to FakeAccounts.TOKEN_ID), accounts.revoked)
        assertNull(store.account)
        assertEquals(SessionState.SignedOut(null), sessions.state.value)
    }

    @Test
    fun aTokenAlreadyRevokedSignsOutQuietly() = runTest {
        store.account = anAccount
        accounts.revokeAnswer = { Revocation.AlreadyGone }
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        sessions.signOut()

        assertEquals(SessionState.SignedOut(null), sessions.state.value)
    }

    @Test
    fun anUnreachableServerStillSignsThePhoneOutAndSaysWhatIsLeft() = runTest {
        store.account = anAccount
        accounts.revokeAnswer = { throw AccountError.Unreachable(address("https://cylist.in"), IOException("down")) }
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        sessions.signOut()

        assertNull(store.account)
        val notice = (sessions.state.value as SessionState.SignedOut).notice!!
        assertTrue(notice, notice.startsWith("Signed out of this phone. Can't reach cylist.in."))
        assertTrue(notice, "\"Pixel 9 (Android)\" may still be in the web app's token list" in notice)
    }

    @Test
    fun aRefusedTokenSignsOutWithANotice() = runTest {
        store.account = anAccount
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

        sessions.current()!!.refused()

        assertNull(store.account)
        assertEquals(
            SessionState.SignedOut("cylist.in no longer accepts this phone's sign-in. Sign in again."),
            sessions.state.value,
        )
    }

    @Test
    fun aRefusalFromAnEarlierSessionDoesNotSignOutTheNextOne() = runTest {
        store.account = anAccount
        val sessions = sessions(UnconfinedTestDispatcher(testScheduler))
        val old = sessions.current()!!
        sessions.signOut()
        val fresh = sessions.signIn(address("https://cylist.in"), "aditi@example.com", "pw")

        old.refused()

        assertSame(fresh, sessions.current())
        assertTrue(store.account != null)
    }

    @Test
    fun a401FromTheServerSignsOut() = runTest {
        MockWebServer().use { server ->
            server.start()
            server.enqueue(MockResponse.Builder().code(401).addHeader("Content-Type", "application/json").body("{}").build())
            store.account = anAccount.copy(server = server.url("/").toString().trimEnd('/'))
            val sessions = sessions(UnconfinedTestDispatcher(testScheduler))

            runCatching { sessions.current()!!.client.api(::AuthApi).meApiV1MeGet() }

            assertTrue(sessions.state.value is SessionState.SignedOut)
            assertNull(store.account)
        }
    }
}
