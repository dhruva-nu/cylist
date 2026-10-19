package cylist.app.docs.markdown

import org.commonmark.ext.autolink.AutolinkExtension
import org.commonmark.ext.footnotes.FootnoteDefinition
import org.commonmark.ext.footnotes.FootnoteReference
import org.commonmark.ext.footnotes.FootnotesExtension
import org.commonmark.ext.gfm.strikethrough.Strikethrough
import org.commonmark.ext.gfm.strikethrough.StrikethroughExtension
import org.commonmark.ext.gfm.tables.TableBlock
import org.commonmark.ext.gfm.tables.TableCell
import org.commonmark.ext.gfm.tables.TableRow
import org.commonmark.ext.gfm.tables.TablesExtension
import org.commonmark.ext.task.list.items.TaskListItemMarker
import org.commonmark.ext.task.list.items.TaskListItemsExtension
import org.commonmark.node.BlockQuote
import org.commonmark.node.BulletList
import org.commonmark.node.Code
import org.commonmark.node.Emphasis
import org.commonmark.node.FencedCodeBlock
import org.commonmark.node.HardLineBreak
import org.commonmark.node.Heading
import org.commonmark.node.HtmlBlock
import org.commonmark.node.HtmlInline
import org.commonmark.node.Image
import org.commonmark.node.IndentedCodeBlock
import org.commonmark.node.Link
import org.commonmark.node.ListBlock
import org.commonmark.node.ListItem
import org.commonmark.node.Node
import org.commonmark.node.OrderedList
import org.commonmark.node.Paragraph
import org.commonmark.node.SoftLineBreak
import org.commonmark.node.StrongEmphasis
import org.commonmark.node.Text
import org.commonmark.node.ThematicBreak
import org.commonmark.parser.Parser

/*
 * A doc's markdown, read the way the web app reads it.
 *
 * The web draws docs with react-markdown and remark-gfm (frontend/src/
 * components/Markdown.tsx): CommonMark, plus GFM's tables, task lists,
 * strikethrough, autolinked URLs and footnotes. commonmark-java is a parser
 * for the same spec with the same five extensions, so a doc parses into the
 * same tree on both. The tree is turned into the plain model below, which
 * MarkdownView draws and which the tests can read without Android.
 *
 * Raw HTML is kept as its source text and drawn as text, as react-markdown
 * does without rehype-raw: a doc an agent wrote cannot put markup on the
 * page of whoever reads it.
 */

/** Inline content: the runs of text inside a paragraph, heading, list item or table cell. */
sealed interface Inline {
    data class Plain(val text: String) : Inline
    data class Code(val text: String) : Inline
    data class Emphasis(val children: List<Inline>) : Inline
    data class Strong(val children: List<Inline>) : Inline
    data class Strike(val children: List<Inline>) : Inline

    /** [url] as written; [MarkdownLinks.resolve] says where it goes, if anywhere. */
    data class Link(val url: String, val children: List<Inline>) : Inline

    /** Drawn as its alt text, linked to the image: the phone loads no images from a doc. */
    data class Image(val url: String, val alt: String) : Inline

    /** A footnote reference, numbered in the order the doc first refers to each note, as remark-gfm numbers them. */
    data class FootnoteRef(val number: Int) : Inline
    data object SoftBreak : Inline
    data object HardBreak : Inline
}

/**
 * A block of a doc. [id] is the block's place in a walk of the whole tree,
 * parents before children, so a block found by [MarkdownDoc.sectionMark] can
 * be pointed at without holding the block itself.
 */
sealed interface Block {
    val id: Int

    data class Heading(override val id: Int, val level: Int, val inlines: List<Inline>) : Block
    data class Paragraph(override val id: Int, val inlines: List<Inline>) : Block
    data class Code(override val id: Int, val language: String?, val text: String) : Block
    data class Quote(override val id: Int, val blocks: List<Block>) : Block
    data class Rule(override val id: Int) : Block

    /** Raw HTML, kept as written and drawn as text. */
    data class Html(override val id: Int, val text: String) : Block

    data class ListBlock(
        override val id: Int,
        val ordered: Boolean,
        val start: Int,
        /** A tight list's items sit close; a loose one's are spaced like paragraphs. */
        val tight: Boolean,
        val items: List<Item>,
    ) : Block

    /** [checked] is null for an ordinary item and true or false for a GFM task. */
    data class Item(override val id: Int, val checked: Boolean?, val blocks: List<Block>) : Block

    data class Table(
        override val id: Int,
        val alignments: List<Align>,
        val header: List<List<Inline>>,
        val rows: List<List<List<Inline>>>,
    ) : Block

    /** The footnotes, gathered at the foot of the doc in the order they were first referred to. */
    data class Footnotes(override val id: Int, val notes: List<Note>) : Block

    data class Note(override val id: Int, val number: Int, val blocks: List<Block>) : Block
}

enum class Align { Start, Center, End }

/** Where an answer's section is in a doc: the block to mark, and the top-level block to scroll to. */
data class SectionMark(val blockId: Int, val topIndex: Int)

/** A parsed doc. Parse with [MarkdownDoc.parse]. */
class MarkdownDoc private constructor(val blocks: List<Block>) {

    /** The level-two headings, for the reader's "On this page", with where each sits in [blocks]. */
    val contents: List<Pair<String, Int>> by lazy {
        blocks.mapIndexedNotNull { index, block ->
            (block as? Block.Heading)?.takeIf { it.level == 2 }?.let { plainText(it.inlines).trim() to index }
        }
    }

    /**
     * The block an answer's [section] points into, found by its text: the
     * markdown carries no anchors. A port of `markSection` in the web's
     * DocReader.tsx, so the phone marks the block the web would: a heading
     * (h1 to h4) whose text is the section's title first, before an "## Index"
     * entry that starts with the same words, and otherwise the list item or
     * paragraph a section jev-docs made from it opens with. A trailing "…"
     * on the title (jev-docs shortens long ones) is dropped, and case is ignored.
     */
    fun sectionMark(section: String): SectionMark? {
        val wanted = section.removeSuffix("…").trim().lowercase()
        if (wanted.isEmpty()) return null
        val walk = blocks.flatMapIndexed { index, block -> preorder(block).map { it to index } }
        val textOf = { block: Block -> blockText(block).trim().lowercase() }
        val found = walk.firstOrNull { (block, _) -> block is Block.Heading && block.level <= 4 && textOf(block) == wanted }
            ?: walk.firstOrNull { (block, _) -> (block is Block.Item || block is Block.Paragraph) && textOf(block).startsWith(wanted) }
        return found?.let { (block, index) -> SectionMark(block.id, index) }
    }

    companion object {
        private val parser: Parser = Parser.builder()
            .extensions(
                listOf(
                    TablesExtension.create(),
                    StrikethroughExtension.create(),
                    TaskListItemsExtension.create(),
                    AutolinkExtension.create(),
                    FootnotesExtension.create(),
                ),
            )
            .build()

        fun parse(source: String): MarkdownDoc = Converter().convert(parser.parse(source))

        /** Everything a reader would see of [inlines], as text: `textContent`, near enough. */
        fun plainText(inlines: List<Inline>): String = buildString { inlines.forEach { appendInline(it) } }

        /** A block's text, its children's run together, as the DOM's `textContent` runs them. */
        fun blockText(block: Block): String = when (block) {
            is Block.Heading -> plainText(block.inlines)
            is Block.Paragraph -> plainText(block.inlines)
            is Block.Code -> block.text
            is Block.Html -> block.text
            is Block.Rule -> ""
            is Block.Quote -> block.blocks.joinToString("", transform = ::blockText)
            is Block.ListBlock -> block.items.joinToString("", transform = ::blockText)
            is Block.Item -> block.blocks.joinToString("", transform = ::blockText)
            is Block.Note -> block.blocks.joinToString("", transform = ::blockText)
            is Block.Footnotes -> block.notes.joinToString("", transform = ::blockText)
            is Block.Table -> (listOf(block.header) + block.rows).flatten().joinToString("") { plainText(it) }
        }

        private fun StringBuilder.appendInline(inline: Inline) {
            when (inline) {
                is Inline.Plain -> append(inline.text)
                is Inline.Code -> append(inline.text)
                is Inline.Emphasis -> inline.children.forEach { appendInline(it) }
                is Inline.Strong -> inline.children.forEach { appendInline(it) }
                is Inline.Strike -> inline.children.forEach { appendInline(it) }
                is Inline.Link -> inline.children.forEach { appendInline(it) }
                is Inline.Image -> append(inline.alt)
                is Inline.FootnoteRef -> append(inline.number)
                Inline.SoftBreak -> append('\n')
                Inline.HardBreak -> append('\n')
            }
        }

        private fun preorder(block: Block): List<Block> = listOf(block) + when (block) {
            is Block.Quote -> block.blocks.flatMap(::preorder)
            is Block.ListBlock -> block.items.flatMap(::preorder)
            is Block.Item -> block.blocks.flatMap(::preorder)
            is Block.Footnotes -> block.notes.flatMap(::preorder)
            is Block.Note -> block.blocks.flatMap(::preorder)
            else -> emptyList()
        }
    }

    /** commonmark-java's tree to the model above. One per parse: it numbers blocks and footnotes as it goes. */
    private class Converter {
        private var nextId = 0
        private val definitions = mutableMapOf<String, FootnoteDefinition>()
        private val footnoteOrder = mutableListOf<String>()

        fun convert(document: Node): MarkdownDoc {
            children(document).filterIsInstance<FootnoteDefinition>().forEach { definitions.putIfAbsent(it.label.lowercase(), it) }
            val blocks = children(document).mapNotNull(::block).toMutableList()
            // Notes are numbered as they are first referred to, and a note can refer
            // to another, so the list grows while it is being drawn.
            val notes = mutableListOf<Block.Note>()
            val footnotesId = if (footnoteOrder.isNotEmpty()) id() else -1
            var index = 0
            while (index < footnoteOrder.size) {
                val label = footnoteOrder[index]
                val noteId = id()
                notes += Block.Note(noteId, index + 1, children(checkNotNull(definitions[label])).mapNotNull(::block))
                index++
            }
            if (notes.isNotEmpty()) blocks += Block.Footnotes(footnotesId, notes)
            return MarkdownDoc(blocks)
        }

        private fun id(): Int = nextId++

        private fun block(node: Node): Block? = when (node) {
            is Heading -> Block.Heading(id(), node.level, inlines(node))
            is Paragraph -> Block.Paragraph(id(), inlines(node))
            is FencedCodeBlock -> Block.Code(id(), node.info?.trim()?.substringBefore(' ')?.ifEmpty { null }, node.literal.removeSuffix("\n"))
            is IndentedCodeBlock -> Block.Code(id(), null, node.literal.removeSuffix("\n"))
            is BlockQuote -> id().let { Block.Quote(it, children(node).mapNotNull(::block)) }
            is ThematicBreak -> Block.Rule(id())
            is HtmlBlock -> Block.Html(id(), node.literal.removeSuffix("\n"))
            is ListBlock -> list(node)
            is TableBlock -> table(node)
            // A definition is drawn at the foot, where it was referred to from;
            // one nothing refers to is not drawn at all, as on the web.
            is FootnoteDefinition -> null
            else -> null
        }

        private fun list(node: ListBlock): Block.ListBlock {
            val listId = id()
            val items = children(node).filterIsInstance<ListItem>().map { item ->
                val itemId = id()
                val marker = children(item).firstOrNull() as? TaskListItemMarker
                    ?: children(item).firstOrNull()?.let { children(it).firstOrNull() as? TaskListItemMarker }
                Block.Item(itemId, marker?.isChecked, children(item).mapNotNull(::block))
            }
            val start = (node as? OrderedList)?.markerStartNumber ?: 1
            return Block.ListBlock(listId, node is OrderedList, start, node.isTight, items)
        }

        private fun table(node: TableBlock): Block.Table {
            val tableId = id()
            val rows = descendants(node).filterIsInstance<TableRow>().map { row -> children(row).filterIsInstance<TableCell>() }
            val head = rows.firstOrNull { cells -> cells.any { it.isHeader } }.orEmpty()
            val alignments = head.map { cell ->
                when (cell.alignment) {
                    TableCell.Alignment.CENTER -> Align.Center
                    TableCell.Alignment.RIGHT -> Align.End
                    else -> Align.Start
                }
            }
            return Block.Table(
                tableId,
                alignments,
                head.map(::inlines),
                rows.filter { cells -> cells.none { it.isHeader } }.map { cells -> cells.map(::inlines) },
            )
        }

        private fun inlines(parent: Node): List<Inline> = children(parent).mapNotNull(::inline)

        private fun inline(node: Node): Inline? = when (node) {
            is Text -> Inline.Plain(node.literal)
            is Code -> Inline.Code(node.literal)
            is Emphasis -> Inline.Emphasis(inlines(node))
            is StrongEmphasis -> Inline.Strong(inlines(node))
            is Strikethrough -> Inline.Strike(inlines(node))
            is Link -> Inline.Link(node.destination, inlines(node))
            is Image -> Inline.Image(node.destination, plainText(inlines(node)))
            is SoftLineBreak -> Inline.SoftBreak
            is HardLineBreak -> Inline.HardBreak
            is HtmlInline -> Inline.Plain(node.literal)
            is FootnoteReference -> footnote(node)
            // The task marker is drawn by the item as a checkbox, not as text.
            is TaskListItemMarker -> null
            else -> null
        }

        private fun footnote(node: FootnoteReference): Inline {
            val label = node.label.lowercase()
            if (label !in definitions) return Inline.Plain("[^${node.label}]")
            if (label !in footnoteOrder) footnoteOrder += label
            return Inline.FootnoteRef(footnoteOrder.indexOf(label) + 1)
        }

        private fun children(node: Node): List<Node> = generateSequence(node.firstChild) { it.next }.toList()

        private fun descendants(node: Node): List<Node> = children(node).flatMap { listOf(it) + descendants(it) }
    }
}
