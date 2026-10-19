package cylist.app.theme

import androidx.compose.ui.graphics.toArgb
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The Compose theme against frontend/src/theme/tokens.css, which is where every
 * value is decided. A token changed on the web and not here, or added there and
 * forgotten here, fails this.
 */
class ThemeTokensTest {
    private val css: String = File(
        checkNotNull(System.getProperty("cylist.webTokens")) { "run through Gradle, which passes the path" },
    ).readText()

    /** Every `--name: #rrggbb;` in the file, by name. */
    private val hexes: Map<String, Int> = Regex("""--([a-z0-9-]+):\s*#([0-9a-fA-F]{6});""")
        .findAll(css)
        .associate { it.groupValues[1] to (0xFF000000 or it.groupValues[2].toLong(16)).toInt() }

    /** What `:root[data-theme='dark']` points each token at: `bg` -> `dark-bg`. */
    private val darkRemaps: Map<String, String> = Regex("""--([a-z0-9-]+):\s*var\(--([a-z0-9-]+)\)""")
        .findAll(
            checkNotNull(Regex(""":root\[data-theme='dark']\s*\{([^}]*)}""").find(css)) {
                "tokens.css has no :root[data-theme='dark'] block"
            }.groupValues[1],
        )
        .associate { it.groupValues[1] to it.groupValues[2] }

    private fun px(prefix: String): Map<String, Float> = Regex("""--($prefix-[a-z0-9]+):\s*([0-9.]+)px;""")
        .findAll(css)
        .associate { it.groupValues[1] to it.groupValues[2].toFloat() }

    @Test
    fun lightColoursMatchTheWeb() {
        LightColors.byCssName().forEach { (name, colour) ->
            assertEquals("--$name", hexes[name]?.hex(), colour.toArgb().hex())
        }
    }

    @Test
    fun darkColoursMatchTheWeb() {
        DarkColors.byCssName().forEach { (name, colour) ->
            // A token the dark block does not remap keeps its light value.
            val expected = hexes[darkRemaps[name] ?: name]
            assertEquals("dark --$name", expected?.hex(), colour.toArgb().hex())
        }
    }

    @Test
    fun everyWebColourIsHere() {
        val ours = LightColors.byCssName().keys
        val missing = hexes.keys.filterNot { it.startsWith("dark-") || it in ours }
        assertTrue("in tokens.css but not in Tokens.kt: $missing", missing.isEmpty())
    }

    @Test
    fun everyDarkRemapIsHere() {
        // Remaps to something other than a hex (`--shadow`, `--scrim`) are not colours here.
        val missing = darkRemaps.filter { (_, target) -> target in hexes }.keys - LightColors.byCssName().keys
        assertTrue("remapped in tokens.css but not in Tokens.kt: $missing", missing.isEmpty())
    }

    @Test
    fun textSizesMatchTheWeb() {
        assertEquals(px("text"), TextSizes.byCssName().mapValues { it.value.value })
    }

    @Test
    fun radiiMatchTheWeb() {
        // `--radius-pill` is 999px only to mean "fully round"; Compose says that with a shape.
        assertEquals(px("radius") - "radius-pill", Radii.byCssName().mapValues { it.value.value })
    }

    private fun Int.hex() = "#%06X".format(this and 0xFFFFFF)
}
