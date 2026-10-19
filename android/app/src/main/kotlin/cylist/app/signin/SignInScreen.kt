package cylist.app.signin

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.systemBarsPadding
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.selection.selectableGroup
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.autofill.ContentType
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentType
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.Radii
import cylist.app.theme.TextSizes

/** The first-run flow: choose a server, then sign in to it. Shown whenever the app is signed out. */
@Composable
fun SignInScreen(viewModel: SignInViewModel, notice: String?, tokenName: String) {
    val state by viewModel.state.collectAsStateWithLifecycle()
    SignInContent(
        state = state,
        notice = notice,
        tokenName = tokenName,
        actions = SignInActions(
            choose = viewModel::choose,
            chooseCustom = viewModel::chooseCustom,
            editCustomUrl = viewModel::editCustomUrl,
            continueToSignIn = viewModel::continueToSignIn,
            editEmail = viewModel::editEmail,
            editPassword = viewModel::editPassword,
            signIn = viewModel::signIn,
            back = viewModel::back,
        ),
    )
}

class SignInActions(
    val choose: (ServerPreset) -> Unit = {},
    val chooseCustom: () -> Unit = {},
    val editCustomUrl: (String) -> Unit = {},
    val continueToSignIn: () -> Unit = {},
    val editEmail: (String) -> Unit = {},
    val editPassword: (String) -> Unit = {},
    val signIn: () -> Unit = {},
    val back: () -> Unit = {},
)

/** The screen with nothing behind it but [state], so previews and screenshots can draw it. */
@Composable
fun SignInContent(state: SignInState, notice: String?, tokenName: String, actions: SignInActions) {
    val colors = CylistTheme.colors
    Box(
        Modifier.fillMaxSize().background(colors.bg).systemBarsPadding().imePadding().verticalScroll(rememberScrollState()),
        contentAlignment = Alignment.TopCenter,
    ) {
        Column(
            Modifier.widthIn(max = 440.dp).fillMaxWidth().padding(horizontal = 16.dp, vertical = 24.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Wordmark()
            if (notice != null) Notice(notice)
            Column(
                Modifier
                    .fillMaxWidth()
                    .background(colors.paper, RoundedCornerShape(Radii.card))
                    .border(1.dp, colors.line, RoundedCornerShape(Radii.card))
                    .padding(20.dp),
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                when (state.step) {
                    SignInState.Step.Server -> ServerStep(state, actions)
                    SignInState.Step.Credentials -> CredentialsStep(state, tokenName, actions)
                }
            }
        }
    }
}

@Composable
private fun Wordmark() {
    val colors = CylistTheme.colors
    Row(
        Modifier.padding(top = 24.dp, bottom = 8.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        Box(Modifier.size(32.dp).background(colors.brand, RoundedCornerShape(Radii.control)), contentAlignment = Alignment.Center) {
            Text("C", color = colors.onBrand, fontSize = TextSizes.xl, fontWeight = FontWeight.ExtraBold, fontFamily = Figtree)
        }
        Text("Cylist", color = colors.ink, fontSize = TextSizes.display, fontWeight = FontWeight.ExtraBold, fontFamily = Figtree)
    }
}

@Composable
private fun Notice(text: String) {
    val colors = CylistTheme.colors
    Text(
        text,
        color = colors.ink,
        fontSize = TextSizes.md,
        fontFamily = Figtree,
        modifier = Modifier
            .fillMaxWidth()
            .background(colors.holdSoft, RoundedCornerShape(Radii.control))
            .padding(horizontal = 14.dp, vertical = 10.dp),
    )
}

@Composable
private fun Heading(text: String) {
    Text(text, color = CylistTheme.colors.ink, fontSize = TextSizes.xl2, fontWeight = FontWeight.Bold, fontFamily = Figtree)
}

@Composable
private fun Muted(text: String) {
    Text(text, color = CylistTheme.colors.ink2, fontSize = TextSizes.md, fontFamily = Figtree, lineHeight = 18.sp)
}

@Composable
private fun ServerStep(state: SignInState, actions: SignInActions) {
    val colors = CylistTheme.colors
    Heading("Choose a server")
    Muted("Which Cylist this phone signs in to. You can sign out and pick another later.")
    Column(Modifier.selectableGroup()) {
        SERVER_PRESETS.forEach { preset ->
            ServerRow(
                name = preset.name,
                detail = preset.address.display,
                selected = (state.choice as? ServerChoice.Preset)?.preset == preset,
                enabled = !state.busy,
                onClick = { actions.choose(preset) },
            )
        }
        ServerRow(
            name = "Another server",
            detail = "Type its address",
            selected = state.choice == ServerChoice.Custom,
            enabled = !state.busy,
            onClick = actions.chooseCustom,
        )
    }
    if (state.choice == ServerChoice.Custom) {
        OutlinedTextField(
            value = state.customUrl,
            onValueChange = actions.editCustomUrl,
            label = { Text("Server address", fontFamily = Figtree) },
            placeholder = { Text("https://cylist.example.com", fontFamily = Figtree, color = colors.ink3) },
            singleLine = true,
            enabled = !state.busy,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go, autoCorrectEnabled = false),
            keyboardActions = KeyboardActions(onGo = { actions.continueToSignIn() }),
            colors = fieldColors(),
            modifier = Modifier.fillMaxWidth(),
        )
    }
    state.error?.let { ErrorText(it) }
    PrimaryButton(
        label = if (state.busy) "Checking the server…" else "Continue",
        busy = state.busy,
        enabled = !state.busy,
        onClick = actions.continueToSignIn,
    )
}

@Composable
private fun ServerRow(name: String, detail: String, selected: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Row(
        Modifier
            .fillMaxWidth()
            .heightIn(min = 48.dp)
            .selectable(selected = selected, enabled = enabled, role = Role.RadioButton, onClick = onClick)
            .padding(vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Box(
            Modifier.size(20.dp).border(2.dp, if (selected) colors.ink else colors.line2, CircleShape),
            contentAlignment = Alignment.Center,
        ) {
            if (selected) Box(Modifier.size(10.dp).background(colors.ink, CircleShape))
        }
        Column(Modifier.weight(1f)) {
            Text(name, color = colors.ink, fontSize = TextSizes.base, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            Text(detail, color = colors.ink3, fontSize = TextSizes.sm, fontFamily = Figtree)
        }
    }
}

@Composable
private fun CredentialsStep(state: SignInState, tokenName: String, actions: SignInActions) {
    val colors = CylistTheme.colors
    Row(verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Heading("Sign in")
            Text(state.server?.display.orEmpty(), color = colors.ink2, fontSize = TextSizes.md, fontFamily = Figtree)
        }
        TextButton(onClick = actions.back) {
            Text("Change server", color = colors.link, fontFamily = Figtree, fontWeight = FontWeight.SemiBold)
        }
    }
    OutlinedTextField(
        value = state.email,
        onValueChange = actions.editEmail,
        label = { Text("Email", fontFamily = Figtree) },
        singleLine = true,
        enabled = !state.busy,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email, imeAction = ImeAction.Next, autoCorrectEnabled = false),
        colors = fieldColors(),
        modifier = Modifier.fillMaxWidth().semantics { contentType = ContentType.Username + ContentType.EmailAddress },
    )
    OutlinedTextField(
        value = state.password,
        onValueChange = actions.editPassword,
        label = { Text("Password", fontFamily = Figtree) },
        singleLine = true,
        enabled = !state.busy,
        visualTransformation = PasswordVisualTransformation(),
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password, imeAction = ImeAction.Go, autoCorrectEnabled = false),
        keyboardActions = KeyboardActions(onGo = { actions.signIn() }),
        colors = fieldColors(),
        modifier = Modifier.fillMaxWidth().semantics { contentType = ContentType.Password },
    )
    state.error?.let { ErrorText(it) }
    PrimaryButton(
        label = if (state.busy) "Signing in…" else "Sign in",
        busy = state.busy,
        enabled = state.canSubmit,
        onClick = actions.signIn,
    )
    Muted("This phone gets a token of its own, \"$tokenName\", which you can revoke from the web app. Your password is not kept.")
}

@Composable
private fun ErrorText(text: String) {
    Text(text, color = CylistTheme.colors.blocked, fontSize = TextSizes.md, fontFamily = Figtree, fontWeight = FontWeight.Medium)
}

@Composable
private fun PrimaryButton(label: String, busy: Boolean, enabled: Boolean, onClick: () -> Unit) {
    val colors = CylistTheme.colors
    Button(
        onClick = onClick,
        enabled = enabled,
        colors = ButtonDefaults.buttonColors(
            containerColor = colors.go,
            contentColor = colors.goInk,
            disabledContainerColor = colors.go.copy(alpha = 0.55f),
            disabledContentColor = colors.goInk,
        ),
        shape = RoundedCornerShape(Radii.control),
        modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp),
    ) {
        if (busy) {
            CircularProgressIndicator(color = colors.goInk, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
            Box(Modifier.size(8.dp))
        }
        Text(label, fontFamily = Figtree, fontWeight = FontWeight.SemiBold, fontSize = TextSizes.base)
    }
}

@Composable
private fun fieldColors() = CylistTheme.colors.let { colors ->
    OutlinedTextFieldDefaults.colors(
        focusedBorderColor = colors.ink,
        unfocusedBorderColor = colors.line2,
        cursorColor = colors.ink,
        focusedLabelColor = colors.ink,
        unfocusedLabelColor = colors.ink2,
        focusedTextColor = colors.ink,
        unfocusedTextColor = colors.ink,
    )
}
