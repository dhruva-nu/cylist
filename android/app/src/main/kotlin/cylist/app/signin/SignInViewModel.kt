package cylist.app.signin

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import cylist.api.AccountError
import cylist.api.AccountService
import cylist.api.ServerAddress
import cylist.app.session.Sessions
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** A server the picker offers by name. */
data class ServerPreset(val name: String, val url: String) {
    val address: ServerAddress
        get() = (ServerAddress.parse(url) as ServerAddress.Parsed.Valid).address
}

/** The servers on this machine's Cloudflare Tunnel (DEPLOY.md), and nothing else built in. */
val SERVER_PRESETS: List<ServerPreset> = listOf(
    ServerPreset("Production", "https://cylist.in"),
    ServerPreset("Staging", "https://stage.cylist.in"),
) + (1..5).map { ServerPreset("Dev $it", "https://dev$it.cylist.in") }

/** Which server the picker has chosen: one of [SERVER_PRESETS], or what is typed in the box. */
sealed interface ServerChoice {
    data class Preset(val preset: ServerPreset) : ServerChoice
    data object Custom : ServerChoice
}

/**
 * The sign-in flow, one screen at a time: pick a server, which is checked
 * before moving on, then sign in to it. Both steps are busy while their call
 * is out, and either ends in [error] rather than a spinner that never stops.
 */
data class SignInState(
    val step: Step = Step.Server,
    val choice: ServerChoice = ServerChoice.Preset(SERVER_PRESETS.first()),
    val customUrl: String = "",
    /** The server the credentials step signs in to: set once the picker's choice has been checked. */
    val server: ServerAddress? = null,
    val email: String = "",
    /** Held for the length of the form only, and cleared when it has been used. */
    val password: String = "",
    val busy: Boolean = false,
    val error: String? = null,
) {
    enum class Step { Server, Credentials }

    val canSubmit: Boolean
        get() = !busy && email.isNotBlank() && password.isNotEmpty()

    override fun toString(): String =
        "SignInState(step=$step, choice=$choice, server=$server, email=$email, busy=$busy, error=$error)"
}

/**
 * Drives [SignInState]. Talks to the server only through [accounts] (to check
 * the server) and [sessions] (to sign in), so it can be tested with fakes.
 *
 * @param cleartextHosts the hosts a custom URL may name over plain HTTP; a
 *   debug build's emulator hosts, and none for a release build.
 */
class SignInViewModel(
    private val sessions: Sessions,
    private val accounts: AccountService,
    private val cleartextHosts: Set<String>,
    private val io: CoroutineDispatcher = Dispatchers.IO,
) : ViewModel() {
    private val _state = MutableStateFlow(SignInState())
    val state: StateFlow<SignInState> = _state.asStateFlow()

    private var call: Job? = null

    fun choose(preset: ServerPreset) = _state.update { it.copy(choice = ServerChoice.Preset(preset), error = null) }

    fun chooseCustom() = _state.update { it.copy(choice = ServerChoice.Custom, error = null) }

    fun editCustomUrl(text: String) = _state.update { it.copy(choice = ServerChoice.Custom, customUrl = text, error = null) }

    fun editEmail(text: String) = _state.update { it.copy(email = text, error = null) }

    fun editPassword(text: String) = _state.update { it.copy(password = text, error = null) }

    /** Check the chosen server answers as a Cylist server, and go on to sign in to it. */
    fun continueToSignIn() {
        val current = _state.value
        if (current.busy || current.step != SignInState.Step.Server) return
        val server = when (val choice = current.choice) {
            is ServerChoice.Preset -> choice.preset.address
            ServerChoice.Custom -> when (val parsed = ServerAddress.parse(current.customUrl, cleartextHosts)) {
                is ServerAddress.Parsed.Valid -> parsed.address
                is ServerAddress.Parsed.Invalid -> {
                    _state.update { it.copy(error = parsed.problem.message) }
                    return
                }
            }
        }
        run(
            work = { withContext(io) { accounts.probe(server) } },
            done = { it.copy(step = SignInState.Step.Credentials, server = server) },
        )
    }

    /** Sign in with the email and password typed. The app's root moves on when [sessions] does. */
    fun signIn() {
        val current = _state.value
        val server = current.server ?: return
        if (!current.canSubmit || current.step != SignInState.Step.Credentials) return
        run(
            work = { sessions.signIn(server, current.email, current.password) },
            done = { it.copy(password = "") },
            failed = { state, error -> if (error is AccountError.WrongPassword) state.copy(password = "") else state },
        )
    }

    /** From signing in back to the picker, dropping whatever was in flight. */
    fun back() {
        call?.cancel()
        _state.update { it.copy(step = SignInState.Step.Server, server = null, password = "", busy = false, error = null) }
    }

    private fun run(
        work: suspend () -> Unit,
        done: (SignInState) -> SignInState,
        failed: (SignInState, AccountError) -> SignInState = { state, _ -> state },
    ) {
        _state.update { it.copy(busy = true, error = null) }
        call = viewModelScope.launch {
            try {
                work()
                _state.update { done(it.copy(busy = false)) }
            } catch (e: AccountError) {
                _state.update { failed(it.copy(busy = false, error = e.message), e) }
            }
        }
    }
}
