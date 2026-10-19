package cylist.app.more

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Text
import androidx.compose.foundation.BorderStroke
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import cylist.app.BuildConfig
import cylist.app.push.NotificationsSection
import cylist.app.session.Account
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.Radii
import cylist.app.theme.TextSizes

/**
 * The More tab. For now it holds the account: who this phone is signed in
 * as, where, under which token, and signing out; and, from CYLIST-107,
 * whether agent notifications can reach this phone (see [NotificationsSection]);
 * and, from CYLIST-108, which release is installed, so a phone updated from
 * the GitHub release page can be checked against it.
 */
@Composable
fun MoreScreen(account: Account, signingOut: Boolean, signOut: () -> Unit) {
    val colors = CylistTheme.colors
    Column(
        Modifier.fillMaxSize().background(colors.bg).statusBarsPadding().verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text("More", color = colors.ink, fontSize = TextSizes.xl3, fontWeight = FontWeight.Bold, fontFamily = Figtree)
        Column(
            Modifier
                .fillMaxWidth()
                .background(colors.paper, RoundedCornerShape(Radii.card))
                .border(1.dp, colors.line, RoundedCornerShape(Radii.card))
                .padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            Text("Signed in as", color = colors.ink3, fontSize = TextSizes.sm, fontFamily = Figtree)
            Text(account.personName, color = colors.ink, fontSize = TextSizes.lg, fontWeight = FontWeight.SemiBold, fontFamily = Figtree)
            account.personEmail?.let { Text(it, color = colors.ink2, fontSize = TextSizes.md, fontFamily = Figtree) }
            Text(
                "on ${account.server.substringAfter("://")}, with the token \"${account.tokenName}\"",
                color = colors.ink2,
                fontSize = TextSizes.md,
                fontFamily = Figtree,
                modifier = Modifier.padding(top = 4.dp),
            )
            OutlinedButton(
                onClick = signOut,
                enabled = !signingOut,
                border = BorderStroke(1.dp, colors.line2),
                colors = ButtonDefaults.outlinedButtonColors(contentColor = colors.blocked),
                shape = RoundedCornerShape(Radii.control),
                modifier = Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(top = 12.dp),
            ) {
                Text(if (signingOut) "Signing out…" else "Sign out", fontFamily = Figtree, fontWeight = FontWeight.SemiBold)
            }
            Text(
                "Signing out revokes this phone's token on the server.",
                color = colors.ink3,
                fontSize = TextSizes.sm,
                fontFamily = Figtree,
            )
        }
        NotificationsSection()
        Text(
            "Version ${BuildConfig.VERSION_NAME}",
            color = colors.ink3,
            fontSize = TextSizes.sm,
            fontFamily = Figtree,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}
