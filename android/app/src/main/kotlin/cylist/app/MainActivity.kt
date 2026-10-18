package cylist.app

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import cylist.app.theme.CylistTheme

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            CylistTheme {
                EmptyScreen()
            }
        }
    }
}

/** The app has no screens yet; this proves the theme and fonts load. */
@Composable
fun EmptyScreen() {
    Surface(modifier = Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
        Box(contentAlignment = Alignment.Center) {
            Text(
                text = stringResource(R.string.app_name),
                style = MaterialTheme.typography.titleLarge,
                color = CylistTheme.colors.ink3,
            )
        }
    }
}

@Preview(name = "Light")
@Composable
private fun EmptyScreenLight() {
    CylistTheme(darkTheme = false) { EmptyScreen() }
}

@Preview(name = "Dark")
@Composable
private fun EmptyScreenDark() {
    CylistTheme(darkTheme = true) { EmptyScreen() }
}
