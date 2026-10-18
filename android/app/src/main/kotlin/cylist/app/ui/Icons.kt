package cylist.app.ui

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

/*
 * The design's icons, from the SVG paths in its artboards (the "Cylist Mobile"
 * canvas), so the phone draws the same strokes as the design and the web app
 * rather than Material's. Each is stroked in black and tinted where it is used.
 */

private fun icon(name: String, viewBox: Float, stroke: Float, vararg paths: String, filled: Boolean = false): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, viewBox, viewBox).apply {
        paths.forEach { d ->
            addPath(
                pathData = addPathNodes(d),
                fill = if (filled) SolidColor(Color.Black) else null,
                stroke = if (filled) null else SolidColor(Color.Black),
                strokeLineWidth = stroke,
                strokeLineCap = StrokeCap.Round,
                strokeLineJoin = StrokeJoin.Round,
            )
        }
    }.build()

object CylistIcons {
    val ChevronDown = icon("chevron-down", 24f, 2.2f, "m6 9 6 6 6-6")
    val ChevronLeft = icon("chevron-left", 24f, 2.2f, "m15 18-6-6 6-6")
    val ChevronRight = icon("chevron-right", 24f, 2.1f, "m9 18 6-6-6-6")
    val Close = icon("close", 24f, 2f, "M6 6l12 12M18 6 6 18")
    val File = icon("file", 24f, 1.9f, "M6 3h8l4 4v14H6z", "M14 3v4h4")
    val Plus = icon("plus", 24f, 2f, "M12 5v14M5 12h14")
    val Filter = icon("filter", 24f, 2f, "M4 7h16M7 12h10M10 17h4")
    val Tick = icon("tick", 16f, 2f, "m3 8.5 3 3 7-7.5")

    val Late = icon("late", 24f, 2.2f, "M12 4 2.8 19.5h18.4zM12 10v4M12 17h.01")
    val Clock = icon("clock", 24f, 2.2f, "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM12 7.5V12l3 2")
    val Calendar = icon("calendar", 24f, 2.2f, "M5 6h14a1 1 0 0 1 1 1v12a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1zM8 4v4M16 4v4M4 11h16")

    val P0 = icon("p0", 16f, 2f, "M4 9.5 8 5.5l4 4M4 13 8 9l4 4")
    val P1 = icon("p1", 16f, 2f, "M4 10.5 8 6.5l4 4")
    val P2 = icon("p2", 16f, 2f, "M4 8h8")

    val Jira = icon("jira", 16f, 1.5f, "M8 2.4 13.6 8 8 13.6 2.4 8Z", "M8 5.6 10.4 8 8 10.4 5.6 8Z")
    val PullRequest = icon(
        "pull-request", 16f, 1.5f,
        "M6.4 4a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        "M6.4 12a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        "M13 12a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        "M4.7 5.7v4.6M11.3 10.3V7.4a2.1 2.1 0 0 0-2.1-2.1H7.4",
    )
    val Blocked = icon("blocked", 24f, 2.2f, "M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18zM5.6 5.6l12.8 12.8")

    val Board = icon("board", 24f, 1.9f, "M6 4h12a2.5 2.5 0 0 1 2.5 2.5v11A2.5 2.5 0 0 1 18 20H6a2.5 2.5 0 0 1-2.5-2.5v-11A2.5 2.5 0 0 1 6 4z", "M9.2 4v16M14.8 4v16")
    val Today = icon("today", 24f, 1.9f, "M6.5 5h11A2.5 2.5 0 0 1 20 7.5v10a2.5 2.5 0 0 1-2.5 2.5h-11A2.5 2.5 0 0 1 4 17.5v-10A2.5 2.5 0 0 1 6.5 5z", "M8.5 3v4M15.5 3v4M4 10h16", "m9.2 14.8 2 2 3.8-3.9")
    val Docs = icon("docs", 24f, 1.9f, "M6 3h8l4 4v14H6z", "M14 3v4h4M9 12h6M9 16h6")
    val People = icon(
        "people", 24f, 1.9f,
        "M16.5 20v-1.5a3.5 3.5 0 0 0-3.5-3.5H7a3.5 3.5 0 0 0-3.5 3.5V20",
        "M13.5 7.5a3.5 3.5 0 1 1-7 0 3.5 3.5 0 0 1 7 0z",
        "M20.5 20v-1.5a3.5 3.5 0 0 0-2.6-3.4",
        "M15.5 4.2a3.5 3.5 0 0 1 0 6.6",
    )
    val More = icon(
        "more", 24f, 0f,
        "M6.7 12a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        "M13.7 12a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        "M20.7 12a1.7 1.7 0 1 1-3.4 0 1.7 1.7 0 0 1 3.4 0z",
        filled = true,
    )
}
