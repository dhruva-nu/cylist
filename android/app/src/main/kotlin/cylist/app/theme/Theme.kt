package cylist.app.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.em
import cylist.app.R

/*
 * Material 3 supplies the components; the look is Cylist's. Every colour role
 * Material reads is pointed at a token, so a stock component never falls back
 * to Material's purple, and tonal elevation is switched off — the web app
 * separates surfaces with borders, not tints and shadows.
 *
 * Screens that need a token Material has no role for (a status, an agent, a
 * priority) read it from CylistTheme.colors.
 */

private fun figtree(weight: Int) =
    Font(R.font.figtree, FontWeight(weight), variationSettings = FontVariation.Settings(FontVariation.weight(weight)))

/** `--font-ui` and `--font-body`, at the five weights the web app loads. */
val Figtree = FontFamily(figtree(400), figtree(500), figtree(600), figtree(700), figtree(800))

/** `--font-mono`: references, counts, timestamps. */
val PlexMono = FontFamily(
    Font(R.font.ibm_plex_mono_regular, FontWeight.Normal),
    Font(R.font.ibm_plex_mono_medium, FontWeight.Medium),
    Font(R.font.ibm_plex_mono_semibold, FontWeight.SemiBold),
)

private fun style(size: TextUnit, weight: FontWeight, lineHeight: Float = 1.4f, tracking: Float = 0f) = TextStyle(
    fontFamily = Figtree,
    fontSize = size,
    fontWeight = weight,
    lineHeight = size * lineHeight,
    letterSpacing = tracking.em,
)

// Headings take the web's -0.015em, and body text its 1.55 line height.
private val CylistTypography = Typography(
    displaySmall = style(TextSizes.display, FontWeight.Bold, 1.2f, -0.015f),
    headlineMedium = style(TextSizes.display, FontWeight.Bold, 1.2f, -0.015f),
    headlineSmall = style(TextSizes.xl4, FontWeight.Bold, 1.25f, -0.015f),
    titleLarge = style(TextSizes.xl3, FontWeight.Bold, 1.3f, -0.015f),
    titleMedium = style(TextSizes.xl, FontWeight.SemiBold, 1.35f),
    titleSmall = style(TextSizes.lg, FontWeight.SemiBold, 1.35f),
    bodyLarge = style(TextSizes.lg, FontWeight.Normal, 1.55f),
    bodyMedium = style(TextSizes.base, FontWeight.Normal, 1.55f),
    bodySmall = style(TextSizes.sm, FontWeight.Normal, 1.5f),
    labelLarge = style(TextSizes.base, FontWeight.SemiBold),
    labelMedium = style(TextSizes.sm, FontWeight.SemiBold),
    labelSmall = style(TextSizes.xs, FontWeight.Medium),
)

private val CylistShapes = Shapes(
    extraSmall = RoundedCornerShape(Radii.sm),
    small = RoundedCornerShape(Radii.control),
    medium = RoundedCornerShape(Radii.card),
    large = RoundedCornerShape(Radii.lg),
    extraLarge = RoundedCornerShape(Radii.lg),
)

private fun CylistColors.toMaterial(dark: Boolean): ColorScheme {
    val base = if (dark) darkColorScheme() else lightColorScheme()
    return base.copy(
        primary = go,
        onPrimary = goInk,
        primaryContainer = brandSoft,
        onPrimaryContainer = brandInk,
        inversePrimary = go,
        secondary = brand,
        onSecondary = onBrand,
        secondaryContainer = brandSoft,
        onSecondaryContainer = brandInk,
        tertiary = agent,
        onTertiary = paper,
        tertiaryContainer = agentSoft,
        onTertiaryContainer = agent,
        background = bg,
        onBackground = ink,
        surface = paper,
        onSurface = ink,
        surfaceVariant = paper2,
        onSurfaceVariant = ink2,
        surfaceTint = Color.Transparent,
        inverseSurface = ink,
        inverseOnSurface = paper,
        error = blocked,
        onError = paper,
        errorContainer = blockedSoft,
        onErrorContainer = blocked,
        outline = line2,
        outlineVariant = line,
        // `--scrim`'s colour; Material applies its own opacity on top.
        scrim = if (dark) Color.Black else ink,
        surfaceBright = paper,
        surfaceDim = bg,
        surfaceContainerLowest = paper,
        surfaceContainerLow = paper,
        surfaceContainer = paper2,
        surfaceContainerHigh = paper2,
        surfaceContainerHighest = paper2,
    )
}

private val LocalCylistColors = staticCompositionLocalOf { LightColors }

@Composable
fun CylistTheme(darkTheme: Boolean = isSystemInDarkTheme(), content: @Composable () -> Unit) {
    val colors = if (darkTheme) DarkColors else LightColors
    CompositionLocalProvider(LocalCylistColors provides colors) {
        MaterialTheme(
            colorScheme = colors.toMaterial(darkTheme),
            typography = CylistTypography,
            shapes = CylistShapes,
            content = content,
        )
    }
}

object CylistTheme {
    /** The full token set, including what Material has no role for. */
    val colors: CylistColors
        @Composable @ReadOnlyComposable
        get() = LocalCylistColors.current
}
