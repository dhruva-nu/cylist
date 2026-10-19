package cylist.app.theme

import androidx.compose.runtime.Immutable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.TextUnit
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/*
 * The web app's design tokens, frontend/src/theme/tokens.css, in Compose. The
 * mobile design (the "Cylist Mobile" canvas) draws with exactly these values,
 * so the phone and the browser are one look rather than two that resemble each
 * other. tokens.css is where a value is decided and explained;
 * ThemeTokensTest fails when the two disagree, so change it there first and
 * copy it here.
 *
 * Names follow the CSS, camel-cased: `--paper-2` is `paper2`.
 */

@Immutable
data class CylistColors(
    // Surfaces
    val bg: Color,
    val paper: Color,
    val paper2: Color,
    val line: Color,
    val line2: Color,
    // Text
    val ink: Color,
    val ink2: Color,
    val ink3: Color,
    // Brand: yellow marks and selects; green confirms.
    val brand: Color,
    val brandInk: Color,
    val brandSoft: Color,
    val go: Color,
    val goHover: Color,
    val goInk: Color,
    val link: Color,
    // Task status
    val ok: Color,
    val okSoft: Color,
    val hold: Color,
    val holdSoft: Color,
    val blocked: Color,
    val blockedSoft: Color,
    // An agent on a card
    val agent: Color,
    val agentSoft: Color,
    val prMerged: Color,
    val focus: Color,
    // Colours that do not flip, because what sits on them does not either.
    val onBrand: Color,
    val onAccent: Color,
    val onAccentDark: Color,
    val filePdf: Color,
    val fileDoc: Color,
    val fileSheet: Color,
    val fileArchive: Color,
    val fileImage: Color,
    val fileOther: Color,
    val fileLink: Color,
    // Priorities, which do flip: each has to be read off the card behind it.
    val priorityP0: Color,
    val priorityP1: Color,
    val priorityP2: Color,
    val priorityP3: Color,
) {
    // Aliases in tokens.css, so they keep flipping with what they borrow from.
    val prRaised: Color get() = ink3
    val prApproved: Color get() = ok

    /** Every token by its CSS name, for ThemeTokensTest. */
    internal fun byCssName(): Map<String, Color> = mapOf(
        "bg" to bg, "paper" to paper, "paper-2" to paper2, "line" to line, "line-2" to line2,
        "ink" to ink, "ink-2" to ink2, "ink-3" to ink3,
        "brand" to brand, "brand-ink" to brandInk, "brand-soft" to brandSoft,
        "go" to go, "go-hover" to goHover, "go-ink" to goInk, "link" to link,
        "ok" to ok, "ok-soft" to okSoft, "hold" to hold, "hold-soft" to holdSoft,
        "blocked" to blocked, "blocked-soft" to blockedSoft,
        "agent" to agent, "agent-soft" to agentSoft, "pr-merged" to prMerged, "focus" to focus,
        "on-brand" to onBrand, "on-accent" to onAccent, "on-accent-dark" to onAccentDark,
        "file-pdf" to filePdf, "file-doc" to fileDoc, "file-sheet" to fileSheet,
        "file-archive" to fileArchive, "file-image" to fileImage, "file-other" to fileOther,
        "file-link" to fileLink,
        "priority-p0" to priorityP0, "priority-p1" to priorityP1,
        "priority-p2" to priorityP2, "priority-p3" to priorityP3,
    )
}

val LightColors = CylistColors(
    bg = Color(0xFFF6F5F1),
    paper = Color(0xFFFFFFFF),
    paper2 = Color(0xFFF2F0EB),
    line = Color(0xFFE6E3DB),
    line2 = Color(0xFFD5D1C7),
    ink = Color(0xFF23272A),
    ink2 = Color(0xFF4B5055),
    ink3 = Color(0xFF686D72),
    brand = Color(0xFFE2B84A),
    brandInk = Color(0xFF5A4300),
    brandSoft = Color(0xFFF7EFD6),
    go = Color(0xFF2E6A45),
    goHover = Color(0xFF25583A),
    goInk = Color(0xFFFFFFFF),
    link = Color(0xFF2F5B86),
    ok = Color(0xFF3D7748),
    okSoft = Color(0xFFEBF2EC),
    hold = Color(0xFF8A5D10),
    holdSoft = Color(0xFFF7F0E2),
    blocked = Color(0xFFA5433B),
    blockedSoft = Color(0xFFF8EBE9),
    agent = Color(0xFF2F6D6A),
    agentSoft = Color(0xFFE9F1F0),
    prMerged = Color(0xFF67589A),
    focus = Color(0xFF23272A),
    onBrand = Color(0xFF3A2E00),
    onAccent = Color(0xFFFFFFFF),
    onAccentDark = Color(0xFF21303A),
    filePdf = Color(0xFFC3533D),
    fileDoc = Color(0xFF3B6FC2),
    fileSheet = Color(0xFF278655),
    fileArchive = Color(0xFF7A6B9E),
    fileImage = Color(0xFFA76900),
    fileOther = Color(0xFF5C6B73),
    fileLink = Color(0xFF4A7B8C),
    priorityP0 = Color(0xFFA5433B),
    priorityP1 = Color(0xFF8A5D10),
    priorityP2 = Color(0xFF3F5F86),
    priorityP3 = Color(0xFF686D72),
)

/** The `--dark-*` values. Whatever tokens.css does not remap stays as in light. */
val DarkColors = LightColors.copy(
    bg = Color(0xFF191B1D),
    paper = Color(0xFF212427),
    paper2 = Color(0xFF292C30),
    line = Color(0xFF2F3337),
    line2 = Color(0xFF3D4247),
    ink = Color(0xFFE8E6E1),
    ink2 = Color(0xFFBAB7B0),
    ink3 = Color(0xFF9A968F),
    brandInk = Color(0xFFECD38F),
    brandSoft = Color(0xFF2F2A1A),
    go = Color(0xFF8AB38E),
    goHover = Color(0xFF9FC4A3),
    goInk = Color(0xFF121A14),
    link = Color(0xFF94B4D6),
    ok = Color(0xFF8AB38E),
    okSoft = Color(0xFF232B24),
    hold = Color(0xFFD6A654),
    holdSoft = Color(0xFF2D281D),
    blocked = Color(0xFFDC8F86),
    blockedSoft = Color(0xFF2F2422),
    agent = Color(0xFF86B5B2),
    agentSoft = Color(0xFF222B2B),
    prMerged = Color(0xFFAB9CC9),
    focus = Color(0xFFE2B84A),
    priorityP0 = Color(0xFFDC8F86),
    priorityP1 = Color(0xFFD6A654),
    priorityP2 = Color(0xFF94A9C4),
    priorityP3 = Color(0xFF9A968F),
)

/** `--text-*`. CSS px become sp, so the phone's font-size setting still applies. */
object TextSizes {
    val xs2: TextUnit = 10.sp
    val xs: TextUnit = 11.sp
    val sm: TextUnit = 12.sp
    val md: TextUnit = 13.sp
    val base: TextUnit = 14.sp
    val lg: TextUnit = 15.sp
    val xl: TextUnit = 16.sp
    val xl2: TextUnit = 18.sp
    val xl3: TextUnit = 20.sp
    val xl4: TextUnit = 22.sp
    val display: TextUnit = 26.sp

    internal fun byCssName(): Map<String, TextUnit> = mapOf(
        "text-2xs" to xs2, "text-xs" to xs, "text-sm" to sm, "text-md" to md,
        "text-base" to base, "text-lg" to lg, "text-xl" to xl, "text-2xl" to xl2,
        "text-3xl" to xl3, "text-4xl" to xl4, "text-display" to display,
    )
}

/** `--radius-*`. `pill` and `round` are shapes in Compose, not numbers. */
object Radii {
    val xs: Dp = 3.dp
    val sm: Dp = 6.dp
    val control: Dp = 8.dp
    val card: Dp = 10.dp
    val lg: Dp = 14.dp

    internal fun byCssName(): Map<String, Dp> = mapOf(
        "radius-xs" to xs, "radius-sm" to sm, "radius-control" to control,
        "radius-card" to card, "radius-lg" to lg,
    )
}
