package cylist.app.push

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.PorterDuff
import android.graphics.PorterDuffXfermode
import android.graphics.Rect
import android.graphics.Typeface
import androidx.annotation.DrawableRes
import androidx.core.content.ContextCompat
import androidx.core.graphics.createBitmap
import androidx.core.graphics.drawable.IconCompat
import cylist.app.R
import kotlin.math.roundToInt

/**
 * The status-bar icons for a [PillMark]. A count or a question mark is cut out
 * of a solid shape, so it shows through as the pill's black like the robot's
 * eyes do; the system tints whatever is left white. Drawn once each and kept.
 */
class PillIcons(private val context: Context) {
    private val drawn = mutableMapOf<PillMark, IconCompat>()

    fun of(mark: PillMark): IconCompat = when (mark) {
        PillMark.Robot -> plain()
        PillMark.Asks -> drawn.getOrPut(mark) { cutOut(R.drawable.ic_stat_agent_bubble, "?", centreY = 10.75f, size = 12f) }
        is PillMark.Count -> drawn.getOrPut(mark) {
            cutOut(R.drawable.ic_stat_agent_head, mark.label, centreY = 14.2f, size = if (mark.label.length > 1) 8.5f else 11f)
        }
    }

    /** The robot alone, as a session's notification in the shade has it. */
    fun plain(): IconCompat = IconCompat.createWithResource(context, R.drawable.ic_stat_agent)

    /**
     * [shape] (a 24×24 status icon) with [glyph] cut out of it, centred on
     * [centreY] in the icon's own units, [size] units tall.
     */
    private fun cutOut(@DrawableRes shape: Int, glyph: String, centreY: Float, size: Float): IconCompat {
        val px = (24 * context.resources.displayMetrics.density).roundToInt()
        val unit = px / 24f
        val bitmap: Bitmap = createBitmap(px, px)
        val canvas = Canvas(bitmap)
        checkNotNull(ContextCompat.getDrawable(context, shape)).apply {
            setBounds(0, 0, px, px)
            draw(canvas)
        }
        val paint = Paint(Paint.ANTI_ALIAS_FLAG).apply {
            typeface = Typeface.DEFAULT_BOLD
            textAlign = Paint.Align.CENTER
            textSize = size * unit
            xfermode = PorterDuffXfermode(PorterDuff.Mode.CLEAR)
        }
        val bounds = Rect().also { paint.getTextBounds(glyph, 0, glyph.length, it) }
        canvas.drawText(glyph, 12f * unit, centreY * unit - bounds.exactCenterY(), paint)
        return IconCompat.createWithBitmap(bitmap)
    }
}
