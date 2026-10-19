package cylist.app.docs.markdown

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.LinkAnnotation
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.TextLinkStyles
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.BaselineShift
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextDecoration
import androidx.compose.ui.text.withLink
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.em
import androidx.compose.ui.unit.sp
import cylist.app.theme.CylistColors
import cylist.app.theme.CylistTheme
import cylist.app.theme.Figtree
import cylist.app.theme.PlexMono
import cylist.app.ui.CylistIcons

/*
 * A parsed doc drawn as the mobile design's reader draws it (the "Cylist
 * Mobile" canvas, Docs artboard, `.md`): body at 15.5sp on --ink-2, headings
 * on --ink, code in Plex Mono on --paper-2, tables and code blocks scrolling
 * sideways inside their own box. What the design does not draw (h3/h4,
 * quotes, rules, task lists, footnotes) follows the web's Markdown.module.css.
 */

/** What every block of one doc is drawn with. */
@Immutable
class MarkdownContext(
    /** The web page the doc is read on, so a relative link goes where the browser would take it. */
    val page: String,
    /** The block an answer pointed into ([SectionMark.blockId]), drawn marked. */
    val marked: Int?,
)

private val BODY_SIZE = 15.5.sp

private fun bodyStyle(colors: CylistColors) = TextStyle(
    fontFamily = Figtree,
    fontSize = BODY_SIZE,
    lineHeight = BODY_SIZE * 1.62f,
    color = colors.ink2,
)

/**
 * One top-level block, with the space it takes below it. The reader puts
 * each in its own lazy row, so a long doc is laid out a screen at a time and
 * a section can be scrolled to by its index.
 */
@Composable
fun MarkdownBlock(block: Block, context: MarkdownContext, modifier: Modifier = Modifier) {
    val top = when {
        block is Block.Heading && block.level <= 2 -> 14.dp
        block is Block.Heading -> 8.dp
        block is Block.Footnotes -> 12.dp
        else -> 0.dp
    }
    Box(modifier.fillMaxWidth().padding(top = top, bottom = 12.dp)) {
        BlockView(block, context)
    }
}

/** Every block of a short piece of markdown, such as the section an answer found, in a plain column. */
@Composable
fun MarkdownColumn(doc: MarkdownDoc, context: MarkdownContext, modifier: Modifier = Modifier) {
    Column(modifier) {
        doc.blocks.forEach { MarkdownBlock(it, context) }
    }
}

@Composable
private fun BlockView(block: Block, context: MarkdownContext) {
    val colors = CylistTheme.colors
    val marked = Modifier.markedIf(block.id == context.marked, colors)
    when (block) {
        is Block.Heading -> Text(
            inlineText(block.inlines, colors, context),
            modifier = marked.semantics { heading() },
            style = headingStyle(block.level, colors),
        )
        is Block.Paragraph -> Text(inlineText(block.inlines, colors, context), modifier = marked, style = bodyStyle(colors))
        is Block.Html -> Text(block.text, modifier = marked, style = bodyStyle(colors))
        is Block.Code -> CodeBlock(block)
        is Block.Quote -> Quote(block, context)
        is Block.Rule -> Box(Modifier.fillMaxWidth().padding(vertical = 12.dp).height(1.dp).background(colors.line))
        is Block.ListBlock -> ListView(block, context)
        is Block.Item -> ItemView(block, marker = "•", tight = true, context = context)
        is Block.Table -> TableView(block, context)
        is Block.Footnotes -> Footnotes(block, context)
        is Block.Note -> Column { block.blocks.forEach { BlockView(it, context) } }
    }
}

private fun headingStyle(level: Int, colors: CylistColors): TextStyle {
    val size = when (level) {
        1 -> 22.sp
        2 -> 18.sp
        3 -> 16.sp
        else -> BODY_SIZE
    }
    return TextStyle(
        fontFamily = Figtree,
        fontSize = size,
        lineHeight = size * 1.3f,
        fontWeight = if (level <= 2) FontWeight.Bold else FontWeight.SemiBold,
        letterSpacing = (-0.01).em,
        color = colors.ink,
    )
}

/** The answered section, marked as the web marks it: --brand-soft spread 6dp past the block's edges. */
private fun Modifier.markedIf(on: Boolean, colors: CylistColors): Modifier = if (!on) this else drawBehind {
    val spread = 6.dp.toPx()
    drawRoundRect(
        color = colors.brandSoft,
        topLeft = Offset(-spread, -spread),
        size = Size(size.width + spread * 2, size.height + spread * 2),
        cornerRadius = CornerRadius(3.dp.toPx()),
    )
}

@Composable
private fun CodeBlock(block: Block.Code) {
    val colors = CylistTheme.colors
    Box(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .background(colors.paper2)
            .border(BorderStroke(1.dp, colors.line), RoundedCornerShape(8.dp))
            .semantics { contentDescription = block.language?.let { "Code, $it" } ?: "Code" }
            .horizontalScroll(rememberScrollState())
            .padding(horizontal = 14.dp, vertical = 12.dp),
    ) {
        Text(
            block.text,
            softWrap = false,
            style = TextStyle(fontFamily = PlexMono, fontSize = 12.5.sp, lineHeight = 12.5.sp * 1.55f, color = colors.ink),
        )
    }
}

@Composable
private fun Quote(block: Block.Quote, context: MarkdownContext) {
    val colors = CylistTheme.colors
    Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min)) {
        Box(Modifier.width(2.dp).fillMaxHeight().background(colors.line2))
        Column(Modifier.padding(start = 14.dp, top = 2.dp, bottom = 2.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            block.blocks.forEach { BlockView(it, context) }
        }
    }
}

@Composable
private fun ListView(block: Block.ListBlock, context: MarkdownContext) {
    Column(verticalArrangement = Arrangement.spacedBy(if (block.tight) 4.dp else 10.dp)) {
        block.items.forEachIndexed { index, item ->
            val marker = if (block.ordered) "${block.start + index}." else "•"
            ItemView(item, marker, block.tight, context)
        }
    }
}

@Composable
private fun ItemView(item: Block.Item, marker: String, tight: Boolean, context: MarkdownContext) {
    val colors = CylistTheme.colors
    Row(Modifier.fillMaxWidth().markedIf(item.id == context.marked, colors)) {
        // The marker takes the body's line height, so it sits on the first line's baseline.
        Box(Modifier.width(if (marker.length > 2) 28.dp else 20.dp)) {
            when (item.checked) {
                null -> Text(marker, style = bodyStyle(colors))
                else -> TaskBox(item.checked)
            }
        }
        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(if (tight) 4.dp else 12.dp)) {
            item.blocks.forEach { BlockView(it, context) }
        }
    }
}

/** A GFM task's box, drawn where the bullet was and not tappable, as the web draws it. */
@Composable
private fun TaskBox(checked: Boolean) {
    val colors = CylistTheme.colors
    Box(
        Modifier
            .padding(top = 5.dp)
            .size(15.dp)
            .clip(RoundedCornerShape(3.dp))
            .background(if (checked) colors.ink2 else colors.paper)
            .border(1.dp, if (checked) colors.ink2 else colors.line2, RoundedCornerShape(3.dp))
            .semantics { contentDescription = if (checked) "Done" else "Not done" },
        contentAlignment = Alignment.Center,
    ) {
        if (checked) Icon(CylistIcons.Tick, null, tint = colors.paper, modifier = Modifier.size(11.dp))
    }
}

@Composable
private fun Footnotes(block: Block.Footnotes, context: MarkdownContext) {
    val colors = CylistTheme.colors
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Box(Modifier.fillMaxWidth().padding(bottom = 6.dp).height(1.dp).background(colors.line))
        block.notes.forEach { note ->
            Row(Modifier.semantics(mergeDescendants = true) { contentDescription = "Footnote ${note.number}" }) {
                Text("${note.number}.", modifier = Modifier.width(24.dp), style = bodyStyle(colors).copy(fontSize = 13.5.sp))
                Column(Modifier.weight(1f)) { note.blocks.forEach { BlockView(it, context) } }
            }
        }
    }
}

/** A table that keeps its columns lined up and scrolls sideways inside its box, as the web's does. */
@Composable
private fun TableView(block: Block.Table, context: MarkdownContext) {
    val colors = CylistTheme.colors
    val columns = maxOf(block.header.size, block.rows.maxOfOrNull { it.size } ?: 0)
    if (columns == 0) return
    val cellStyle = TextStyle(fontFamily = Figtree, fontSize = 13.5.sp, lineHeight = 13.5.sp * 1.45f, color = colors.ink2)
    BoxWithConstraints(
        Modifier
            .fillMaxWidth()
            .clip(RoundedCornerShape(8.dp))
            .border(BorderStroke(1.dp, colors.line), RoundedCornerShape(8.dp)),
    ) {
        val fill = maxWidth
        Box(Modifier.horizontalScroll(rememberScrollState())) {
            val rows = listOf(block.header) + block.rows
            TableGrid(columns, fill, maxCell = 260.dp) {
                rows.forEachIndexed { r, row ->
                    val header = r == 0
                    val last = r == rows.lastIndex
                    for (c in 0 until columns) {
                        val inlines = row.getOrNull(c).orEmpty()
                        Box(
                            Modifier
                                .background(if (header) colors.paper else Color.Transparent)
                                .drawBehind {
                                    if (!last) drawRect(if (header) colors.line2 else colors.line, Offset(0f, size.height - 1.dp.toPx()), Size(size.width, 1.dp.toPx()))
                                }
                                .padding(horizontal = 12.dp, vertical = 8.dp),
                        ) {
                            Text(
                                inlineText(inlines, colors, context),
                                style = if (header) cellStyle.copy(fontWeight = FontWeight.SemiBold, color = colors.ink) else cellStyle,
                                textAlign = when (block.alignments.getOrNull(c)) {
                                    Align.Center -> TextAlign.Center
                                    Align.End -> TextAlign.End
                                    else -> TextAlign.Start
                                },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        }
                    }
                }
            }
        }
    }
}

/**
 * Cells in row order, [columns] to a row. A column is as wide as its widest
 * cell wants, up to [maxCell], after which its text wraps; a row is as tall as
 * its tallest cell. A table narrower than [fill] gives the rest to its last column.
 */
@Composable
private fun TableGrid(columns: Int, fill: Dp, maxCell: Dp, content: @Composable () -> Unit) {
    Layout(content) { cells, _ ->
        val cap = maxCell.roundToPx()
        val rowCount = (cells.size + columns - 1) / columns
        val widths = IntArray(columns) { c ->
            (0 until rowCount).mapNotNull { r -> cells.getOrNull(r * columns + c) }
                .maxOfOrNull { minOf(cap, it.maxIntrinsicWidth(Constraints.Infinity)) } ?: 0
        }
        val extra = fill.roundToPx() - widths.sum()
        if (extra > 0) widths[columns - 1] += extra
        val heights = IntArray(rowCount) { r ->
            (0 until columns).maxOf { c -> cells.getOrNull(r * columns + c)?.minIntrinsicHeight(widths[c]) ?: 0 }
        }
        val placeables = cells.mapIndexed { i, cell -> cell.measure(Constraints.fixed(widths[i % columns], heights[i / columns])) }
        layout(widths.sum(), heights.sum()) {
            var y = 0
            for (r in 0 until rowCount) {
                var x = 0
                for (c in 0 until columns) {
                    placeables.getOrNull(r * columns + c)?.place(x, y)
                    x += widths[c]
                }
                y += heights[r]
            }
        }
    }
}

/** Inline runs as styled text, links tappable and opened in the browser, as the web opens them in a new tab. */
@Composable
fun inlineText(inlines: List<Inline>, colors: CylistColors, context: MarkdownContext): AnnotatedString =
    remember(inlines, colors, context.page) { buildInline(inlines, colors, context.page) }

internal fun buildInline(inlines: List<Inline>, colors: CylistColors, page: String): AnnotatedString = buildAnnotatedString {
    val linkStyles = TextLinkStyles(SpanStyle(color = colors.link, textDecoration = TextDecoration.Underline))
    val codeStyle = SpanStyle(fontFamily = PlexMono, fontSize = 0.86.em, color = colors.ink, background = colors.paper2)

    fun append(inline: Inline) {
        when (inline) {
            is Inline.Plain -> append(inline.text)
            is Inline.Code -> withStyle(codeStyle) { append(" ${inline.text} ") }
            is Inline.Emphasis -> withStyle(SpanStyle(fontStyle = FontStyle.Italic)) { inline.children.forEach(::append) }
            is Inline.Strong -> withStyle(SpanStyle(fontWeight = FontWeight.SemiBold, color = colors.ink)) { inline.children.forEach(::append) }
            is Inline.Strike -> withStyle(SpanStyle(textDecoration = TextDecoration.LineThrough)) { inline.children.forEach(::append) }
            is Inline.Link -> {
                val target = MarkdownLinks.resolve(inline.url, page)
                if (target == null) {
                    inline.children.forEach(::append)
                } else {
                    withLink(LinkAnnotation.Url(target, linkStyles)) { inline.children.forEach(::append) }
                }
            }
            is Inline.Image -> {
                val label = "[${inline.alt.ifBlank { "image" }}]"
                val target = MarkdownLinks.resolve(inline.url, page)
                if (target == null) append(label) else withLink(LinkAnnotation.Url(target, linkStyles)) { append(label) }
            }
            is Inline.FootnoteRef -> withStyle(SpanStyle(baselineShift = BaselineShift.Superscript, fontSize = 0.75.em, color = colors.link)) {
                append(inline.number.toString())
            }
            // A soft break is a space when the paragraph is drawn, as in a browser.
            Inline.SoftBreak -> append(' ')
            Inline.HardBreak -> append('\n')
        }
    }
    inlines.forEach(::append)
}
