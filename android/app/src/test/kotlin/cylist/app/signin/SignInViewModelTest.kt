package cylist.app.signin

import cylist.api.AccountError
import cylist.app.session.FakeAccounts
import cylist.app.session.MemoryStore
import cylist.app.session.SessionState
import cylist.app.session.Sessions
import cylist.app.session.address
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.advanceUntilIdle
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import okhttp3.OkHttpClient
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.IOException

@OptIn(ExperimentalCoroutinesApi::class)
class SignInViewModelTest {
    private val dispatcher: TestDispatcher = StandardTestDispatcher()
    private val accounts = FakeAccounts()
    private val store = MemoryStore()
    private lateinit var sessions: Sessions
    private lateinit var model: SignInViewModel

    @Before
    fun setUp() {
        Dispatchers.setMain(dispatcher)
        sessions = Sessions(store, accounts, "Pixel 9", OkHttpClient(), dispatcher)
        model = SignInViewModel(sessions, accounts, cleartextHosts = setOf("10.0.2.2"), io = dispatcher)
    }

    @After
    fun tearDown() = Dispatchers.resetMain()

    private val state get() = model.state.value

    @Test
    fun opensOnProductionWithEveryPresetOffered() {
        assertEquals(SignInState.Step.Server, state.step)
        assertEquals(ServerChoice.Preset(SERVER_PRESETS.first()), state.choice)
        assertEquals(
            listOf(
                "https://cylist.in", "https://stage.cylist.in", "https://dev1.cylist.in", "https://dev2.cylist.in",
                "https://dev3.cylist.in", "https://dev4.cylist.in", "https://dev5.cylist.in",
            ),
            SERVER_PRESETS.map { it.address.url },
        )
    }

    @Test
    fun aCheckedServerMovesOnToSignIn() = runTest(dispatcher) {
        model.choose(SERVER_PRESETS[1])
        model.continueToSignIn()
        assertTrue("busy while the server is checked", state.busy)

        advanceUntilIdle()

        assertEquals(listOf(address("https://stage.cylist.in")), accounts.probed)
        assertEquals(SignInState.Step.Credentials, state.step)
        assertEquals(address("https://stage.cylist.in"), state.server)
        assertFalse(state.busy)
    }

    @Test
    fun anUnreachableServerSaysSoAndStaysOnThePicker() = runTest(dispatcher) {
        accounts.probeFails = AccountError.Unreachable(address("https://dev4.cylist.in"), IOException("timeout"))
        model.choose(SERVER_PRESETS[5])

        model.continueToSignIn()
        advanceUntilIdle()

        assertEquals(SignInState.Step.Server, state.step)
        assertFalse(state.busy)
        assertEquals("Can't reach dev4.cylist.in. Check the address and your connection.", state.error)
    }

    @Test
    fun aBadCustomAddressIsRefusedWithoutAskingAnyone() = runTest(dispatcher) {
        model.editCustomUrl("http://cylist.in")
        model.continueToSignIn()
        advanceUntilIdle()

        assertEquals(ServerChoice.Custom, state.choice)
        assertEquals("This build talks to servers over https:// only.", state.error)
        assertTrue(accounts.probed.isEmpty())
        assertFalse(state.busy)
    }

    @Test
    fun aCustomAddressOnAnAllowedCleartextHostIsChecked() = runTest(dispatcher) {
        model.editCustomUrl("http://10.0.2.2:8102/")
        model.continueToSignIn()
        advanceUntilIdle()

        assertEquals(listOf(address("http://10.0.2.2:8102")), accounts.probed)
        assertEquals(SignInState.Step.Credentials, state.step)
    }

    @Test
    fun editingClearsTheError() = runTest(dispatcher) {
        model.editCustomUrl("")
        model.continueToSignIn()
        assertEquals("Enter the server's address.", state.error)

        model.editCustomUrl("c")
        assertNull(state.error)
    }

    @Test
    fun signsInAndForgetsThePassword() = runTest(dispatcher) {
        toCredentials()
        assertFalse(state.canSubmit)
        model.editEmail("aditi@example.com")
        model.editPassword("hunter2")
        assertTrue(state.canSubmit)

        model.signIn()
        assertTrue(state.busy)
        advanceUntilIdle()

        assertTrue(sessions.state.value is SessionState.SignedIn)
        assertEquals("", state.password)
        assertEquals("aditi@example.com", accounts.signIns.single().first)
    }

    @Test
    fun aWrongPasswordIsSaidAndClearsThePasswordOnly() = runTest(dispatcher) {
        accounts.signInFails = AccountError.WrongPassword()
        toCredentials()
        model.editEmail("aditi@example.com")
        model.editPassword("wrong")

        model.signIn()
        advanceUntilIdle()

        assertEquals("That email and password do not match an account.", state.error)
        assertEquals("aditi@example.com", state.email)
        assertEquals("", state.password)
        assertEquals(SignInState.Step.Credentials, state.step)
        assertTrue(sessions.state.value is SessionState.SignedOut)
    }

    @Test
    fun aSecondTapWhileBusyDoesNothing() = runTest(dispatcher) {
        toCredentials()
        model.editEmail("aditi@example.com")
        model.editPassword("pw")

        model.signIn()
        model.signIn()
        advanceUntilIdle()

        assertEquals(1, accounts.signIns.size)
    }

    @Test
    fun backDropsTheCallInFlightAndThePassword() = runTest(dispatcher) {
        toCredentials()
        model.editEmail("aditi@example.com")
        model.editPassword("pw")
        model.signIn()
        assertTrue(state.busy)

        model.back()
        advanceUntilIdle()

        assertEquals(SignInState.Step.Server, state.step)
        assertFalse(state.busy)
        assertEquals("", state.password)
        assertTrue(accounts.signIns.isEmpty())
        assertTrue(sessions.state.value is SessionState.SignedOut)
    }

    private fun toCredentials() {
        model.continueToSignIn()
        dispatcher.scheduler.advanceUntilIdle()
        assertEquals(SignInState.Step.Credentials, state.step)
    }
}
